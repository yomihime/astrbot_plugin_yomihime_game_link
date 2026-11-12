"""AstrBot exact-event command output and durable text delivery boundary."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ...core.ports import (
    MessageReceipt,
    MessageStatus,
    MessageTarget,
    PublicGroupRoute,
    RenderedMessage,
)
from ...yomihime_game_link_sdk.subscriptions import ConversationKind, ConversationRef

ImageFactory = Callable[[bytes], object]

PlainFactory = Callable[[str], object]
MessageChainFactory = Callable[[Sequence[object]], object]

_OFFICIAL_QQ_ADAPTERS = ("qq_official", "qq_official_webhook")


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
        """Map a checked durable target to one text and optional public-card chain."""

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
            or (
                (payload.png_bytes is not None or payload.png_pages)
                and target.authorized
            )
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
        lookup = getattr(self._context, "get_platform_inst", None)
        platform, adapter_name = self._platform(lookup, conversation.delivery_route)
        group_route = target.public_group_route
        if group_route is not None and (
            group_route.platform is None
            or platform is not group_route.platform
            or adapter_name != group_route.adapter_name
        ):
            return MessageReceipt(MessageStatus.FAILED)
        try:
            components = (self._plain_factory(payload.text),)
            if payload.png_pages:
                # The Host's Webhook adapter cannot run the shared
                # multi-image send recursion on its own instance.
                unavailable = (
                    adapter_name == "qq_official_webhook" and len(payload.png_pages) > 1
                )
                if not unavailable:
                    try:
                        # Construct the entire set before dispatch.
                        components += tuple(
                            self._image_factory(page) for page in payload.png_pages
                        )
                    except Exception:
                        unavailable = True
                if unavailable:
                    components = (
                        self._plain_factory(
                            payload.text + "\n网页截图暂不可用，请查看原文。"
                        ),
                    )
            elif payload.png_bytes is not None:
                try:
                    components += (self._image_factory(payload.png_bytes),)
                except Exception:
                    # No dispatch has begun. Retain the complete text instead
                    # of losing the announcement to an optional image failure.
                    pass
            chain = self._chain_factory(components)
        except Exception:
            return MessageReceipt(MessageStatus.FAILED)
        # Context's bool confirms adapter dispatch, not a platform receipt.
        # QQ Official may return normally without sending (for example a
        # group with no cached scene after Host restart). Never mark that
        # as SENT or retry it: the remote outcome is genuinely unknown.
        unconfirmed_platform = adapter_name in _OFFICIAL_QQ_ADAPTERS or (
            callable(lookup) and adapter_name is None
        )
        try:
            sent = await send_message(session, chain)
        except Exception:
            # Once dispatch begins, even ValueError may follow a platform send.
            return MessageReceipt(MessageStatus.UNKNOWN)
        if sent is True:
            return MessageReceipt(
                MessageStatus.UNKNOWN
                if unconfirmed_platform
                else MessageStatus.ACCEPTED
            )
        if sent is False:
            return MessageReceipt(MessageStatus.FAILED)
        return MessageReceipt(MessageStatus.UNKNOWN)

    def public_group_route(
        self, conversation: ConversationRef
    ) -> PublicGroupRoute | None:
        """Bind public group dispatch to the adapter used to check deduplication."""
        lookup = getattr(self._context, "get_platform_inst", None)
        if (
            conversation.kind is not ConversationKind.GROUP
            or conversation.adapter_id != conversation.delivery_route
            or not callable(lookup)
        ):
            return None
        platform, adapter_name = self._platform(lookup, conversation.delivery_route)
        if adapter_name not in _OFFICIAL_QQ_ADAPTERS:
            return PublicGroupRoute(platform, adapter_name)
        # AstrBot's isolated group session is sender_id + '_' + group_id.
        # Both official adapters use its final component when sending.
        return PublicGroupRoute(platform, adapter_name, self._qq_group_id)

    @staticmethod
    def _qq_group_id(session_id: str) -> str:
        return session_id.rsplit("_", 1)[-1]

    @staticmethod
    def _platform(lookup, route: str) -> tuple[object | None, str | None]:
        if callable(lookup):
            try:
                platform = lookup(route)
                if platform is not None:
                    name = platform.meta().name
                    if type(name) is str and name:
                        return platform, name
            except Exception:
                pass
        return None, None

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
                or payload.image_profile != "card"
                or payload.resource_ids
                or payload.png_pages
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
            if hasattr(event, "_ygl_command_send_marker"):
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

    def prepare_command(self, ingress, target, payload, *, image_current):
        """Build one exact-event chain before Core's final admission check.

        This method does not send or authorize. Private images require a direct
        target matching the trusted ingress actor. The returned private object
        performs no preparation or asynchronous lookups during dispatch.
        """
        if (
            type(target) is not MessageTarget
            or type(payload) is not RenderedMessage
            or payload.image_profile != "card"
            or payload.resource_ids
            or not payload.png_pages
            or not callable(image_current)
            or self._command_current is None
            or self._command_current(ingress) is not True
        ):
            raise ValueError("invalid prepared command")
        route = target.conversation
        if (
            route is None
            or route.adapter_id != ingress.adapter_id
            or route.delivery_route != ingress.delivery_route
            or route.conversation_id != ingress.conversation_id
            or route.kind is not ingress.conversation_kind
            or target.recipient_id is not None
            and target.recipient_id != ingress.actor_id
            or target.authorized
            and (
                route.kind is not ConversationKind.DIRECT
                or target.recipient_id != ingress.actor_id
            )
        ):
            raise ValueError("prepared command route changed")
        event = ingress.evidence.event
        send = getattr(event, "send", None)
        chain_result = getattr(event, "chain_result", None)
        if not callable(send) or not callable(chain_result):
            raise ValueError("prepared command event is unavailable")
        # Failures here have not dispatched anything; Core can retain complete
        # text fallback. Never construct components after final admission.
        components = tuple(self._image_factory(page) for page in payload.png_pages)
        chain = chain_result(list(components))

        def current():
            image_current()
            if self._command_current(ingress) is not True:
                raise ValueError("prepared command ingress changed")

        current()
        return _PreparedHostCommand(event, chain, send, current)

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


@dataclass(slots=True, eq=False)
class _PreparedHostCommand:
    event: object
    chain: object
    send: Callable
    check_current: Callable[[], None]
    spent: bool = False

    async def dispatch(self) -> MessageReceipt:
        try:
            self.check_current()
            # A marker from any previous runtime also spends this exact event.
            if self.spent or hasattr(self.event, "_ygl_command_send_marker"):
                return MessageReceipt(MessageStatus.UNKNOWN)
            self.spent = True
            # Do not retain the prepared chain (and its base64 images) through
            # the Host event's spent marker.
            setattr(self.event, "_ygl_command_send_marker", object())
        except Exception:
            return MessageReceipt(MessageStatus.FAILED)
        try:
            sent = await self.send(self.chain)
        except Exception:
            return MessageReceipt(MessageStatus.UNKNOWN)
        if sent is True:
            return MessageReceipt(MessageStatus.ACCEPTED)
        if sent is False:
            return MessageReceipt(MessageStatus.FAILED)
        return MessageReceipt(MessageStatus.UNKNOWN)


__all__ = ["AstrBotMessagePort", "ImageFactory", "MessageChainFactory", "PlainFactory"]
