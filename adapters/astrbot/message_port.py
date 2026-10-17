"""AstrBot exact-event command output and durable text delivery boundary."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from yomihime_game_link_sdk.subscriptions import ConversationKind

from ...core.ports import MessageReceipt, MessageStatus, MessageTarget, RenderedMessage

ImageFactory = Callable[[bytes], object]

PlainFactory = Callable[[str], object]
MessageChainFactory = Callable[[Sequence[object]], object]


class AstrBotMessagePort:
    """Send command cards via their event and durable text via Context."""

    def __init__(
        self,
        context: object,
        *,
        plain_factory: PlainFactory | None = None,
        chain_factory: MessageChainFactory | None = None,
        image_factory: ImageFactory | None = None,
        command_current: Callable[[object], bool] | None = None,
    ):
        self._context = context
        self._plain_factory = plain_factory or self._astrbot_plain
        self._chain_factory = chain_factory or self._astrbot_message_chain
        self._image_factory = image_factory or self._astrbot_image
        self._command_current = command_current
        self._command_send_marker = object()

    async def send(
        self, target: MessageTarget, payload: RenderedMessage
    ) -> MessageReceipt:
        """Map only Core's current conversation target to AstrBot text output."""

        if not isinstance(target, MessageTarget) or not isinstance(
            payload, RenderedMessage
        ):
            return MessageReceipt(MessageStatus.FAILED)
        conversation = target.conversation
        if (
            conversation is None
            or conversation.conversation_id != target.conversation_id
            or conversation.adapter_id != conversation.delivery_route
            or payload.resource_ids
            or payload.png_bytes is not None
            or not _valid_session_component(conversation.delivery_route, platform=True)
            or not _valid_session_component(conversation.conversation_id)
        ):
            return MessageReceipt(MessageStatus.FAILED)

        message_type = {
            ConversationKind.DIRECT: "FriendMessage",
            ConversationKind.GROUP: "GroupMessage",
        }.get(conversation.kind)
        if message_type is None:
            return MessageReceipt(MessageStatus.FAILED)
        session = (
            f"{conversation.delivery_route}:{message_type}:"
            f"{conversation.conversation_id}"
        )
        send_message = getattr(self._context, "send_message", None)
        if not callable(send_message):
            return MessageReceipt(MessageStatus.FAILED)
        try:
            plain = self._plain_factory(payload.text)
            chain = self._chain_factory((plain,))
        except Exception:
            return MessageReceipt(MessageStatus.FAILED)
        try:
            sent = await send_message(session, chain)
        except Exception:
            # Once dispatch begins, even ValueError may follow a platform send.
            return MessageReceipt(MessageStatus.UNKNOWN)
        if sent is True:
            return MessageReceipt(MessageStatus.ACCEPTED)
        if sent is False:
            return MessageReceipt(MessageStatus.FAILED)
        return MessageReceipt(MessageStatus.UNKNOWN)

    async def send_command(
        self, ingress, target, payload, *, image_current=None
    ) -> MessageReceipt:
        """Use the exact trusted request event, never a session broadcast.

        Image.fromBytes owns a base64 snapshot. AstrBot may retain an ordinary
        attachment copy; local PNG cleanup does not recall that copy. A None
        send result is UNKNOWN, and must never cause a text retry.
        """
        if not isinstance(target, MessageTarget) or not isinstance(
            payload, RenderedMessage
        ):
            return MessageReceipt(MessageStatus.FAILED)
        try:
            if (
                self._command_current is None
                or self._command_current(ingress) is not True
            ):
                return MessageReceipt(MessageStatus.FAILED)
            route = target.conversation
            if (
                route is None
                or payload.resource_ids
                or (payload.png_bytes is not None and target.authorized)
                or route.adapter_id != ingress.adapter_id
                or route.delivery_route != ingress.delivery_route
                or route.conversation_id != ingress.conversation_id
                or route.kind is not ingress.conversation_kind
                or (
                    target.recipient_id is not None
                    and target.recipient_id != ingress.actor_id
                )
            ):
                return MessageReceipt(MessageStatus.FAILED)
            event = ingress.evidence.event
            send = getattr(event, "send", None)
            chain_result = getattr(event, "chain_result", None)
            if not callable(send) or not callable(chain_result):
                return MessageReceipt(MessageStatus.FAILED)
            if payload.png_bytes is not None:
                try:
                    component = self._image_factory(payload.png_bytes)
                except Exception:
                    # Construction has not dispatched anything; full text is
                    # still safe. No fallback is attempted after event.send.
                    component = self._plain_factory(payload.text)
            else:
                component = self._plain_factory(payload.text)
            chain = chain_result([component])
            if image_current is not None:
                image_current()
            if self._command_current(ingress) is not True:
                return MessageReceipt(MessageStatus.FAILED)
            # The spent marker lives on the event, not in an ever-growing
            # global registry. One command output per exact event/runtime;
            # retained Host events cannot exhaust a global send quota.
            if (
                getattr(event, "_ygl_command_send_marker", None)
                is self._command_send_marker
            ):
                return MessageReceipt(MessageStatus.UNKNOWN)
            setattr(event, "_ygl_command_send_marker", self._command_send_marker)
        except Exception:
            return MessageReceipt(MessageStatus.FAILED)
        try:
            sent = await send(chain)
        except Exception:
            return MessageReceipt(MessageStatus.UNKNOWN)
        if sent is True:
            return MessageReceipt(MessageStatus.ACCEPTED)
        if sent is False:
            return MessageReceipt(MessageStatus.FAILED)
        return MessageReceipt(MessageStatus.UNKNOWN)

    @staticmethod
    def _astrbot_image(content: bytes) -> object:
        from astrbot.api.message_components import Image

        return Image.fromBytes(content)

    @staticmethod
    def _astrbot_plain(text: str) -> object:
        from astrbot.api.message_components import Plain

        return Plain(text)

    @staticmethod
    def _astrbot_message_chain(components: Sequence[object]) -> object:
        from astrbot.api.event import MessageChain

        return MessageChain(list(components))


def _valid_session_component(value: object, *, platform: bool = False) -> bool:
    if type(value) is not str or not value or len(value) > 512:
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    return not (platform and ":" in value)


__all__ = ["AstrBotMessagePort", "ImageFactory", "MessageChainFactory", "PlainFactory"]
