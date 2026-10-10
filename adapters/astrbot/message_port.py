"""AstrBot implementation of Core's durable text delivery boundary."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from yomihime_game_link_sdk.subscriptions import ConversationKind

from ...core.ports import MessageReceipt, MessageStatus, MessageTarget, RenderedMessage

PlainFactory = Callable[[str], object]
MessageChainFactory = Callable[[Sequence[object]], object]


class AstrBotMessagePort:
    """Send Core-rendered text through AstrBot's public Context API."""

    def __init__(
        self,
        context: object,
        *,
        plain_factory: PlainFactory | None = None,
        chain_factory: MessageChainFactory | None = None,
    ):
        self._context = context
        self._plain_factory = plain_factory or self._astrbot_plain
        self._chain_factory = chain_factory or self._astrbot_message_chain

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


__all__ = ["AstrBotMessagePort", "MessageChainFactory", "PlainFactory"]
