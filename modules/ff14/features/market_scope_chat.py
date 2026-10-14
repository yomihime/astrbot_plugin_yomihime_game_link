"""Market-only bounded Universalis World/DC choice tickets."""

import secrets
from dataclasses import dataclass

from ..query_resolution import QueryResolutionError, _region
from .chat_context import PendingTargets
from .market_sources import CatalogSnapshot


@dataclass(frozen=True, slots=True)
class _SelectedCatalog:
    frozen: object
    selected: tuple

    @property
    def entries(self):
        return self.frozen.entries

    @property
    def available(self):
        return self.frozen.available

    def find(self, kind, value):
        for entry in self.selected:
            if (
                kind == entry.kind
                and type(value) is type(entry.id)
                and value == entry.id
            ):
                return entry
        return self.frozen.find(kind, value)


class MarketScopeChoices:
    def __init__(self, *, clock):
        self.pending = PendingTargets(clock=clock)

    def get(self, owner):
        return self.pending.get(owner)

    def invalidate(self, owner):
        self.pending.states.pop(owner, None)

    def publish(self, owner, event_ref, ticket, error, catalog):
        choices = error.choices
        if "region" in error.parameters:
            regions = _region(error.parameters["region"])
            choices = tuple(row for row in choices if row.region in regions)
            if not choices:
                raise QueryResolutionError("服务器/大区候选与明确区域冲突。")
        if not choices or len({row.kind for row in choices}) != 1:
            raise QueryResolutionError("范围候选目录格式无效。")
        previous = self.get(owner)
        inherited = (
            previous.values
            if previous is not None and previous.values["ticket"] == ticket
            else {}
        )
        state = self.pending.begin(
            owner,
            event_ref,
            {
                "parameters": dict(error.parameters),
                "defaults": error.defaults,
                "catalog": catalog,
                "ticket": ticket,
                "event_refs": inherited.get("event_refs", frozenset()) | {event_ref},
                "selected_entries": inherited.get("selected_entries", ()),
            },
        )
        state.candidates = tuple(choices[:6])
        state.batch_id = secrets.token_urlsafe(24)
        state.values["truncated"] = len(choices) > 6
        return state

    def event_query(self, owner, event_ref, action):
        state = self.get(owner)
        if state is None:
            return None
        if event_ref in state.values["event_refs"] and (
            state.consumed or event_ref != state.event_ref
        ):
            raise QueryResolutionError("原始或确认消息不能绕过已消费范围候选。")
        if event_ref == state.event_ref or action in ("continue", "clarify"):
            if state.consumed:
                return None  # A later item-choice batch has its own registry.
            return state
        return None

    def choose(self, owner, event_ref, parameters):
        state = self.get(owner)
        if (
            state is None
            or state.consumed
            or event_ref in state.values["event_refs"]
            or parameters.get("batch_id") != state.batch_id
            or parameters.get("generation") != state.generation
            or parameters.get("scope_kind") != state.candidates[0].kind
        ):
            raise QueryResolutionError("范围候选批次无效或已过期。")
        action = parameters.get("action", "select")
        selected_keys = set(parameters) & {"world_id", "dc_id", "item_id"}
        if action == "clarify" and not selected_keys:
            return state, None
        field = "world_id" if state.candidates[0].kind == "world" else "dc_id"
        if action != "select" or selected_keys != {field}:
            raise QueryResolutionError("请明确一个同 namespace 的范围候选。")
        value = parameters[field]
        if (field == "world_id" and type(value) is not int) or (
            field == "dc_id" and type(value) is not str
        ):
            raise QueryResolutionError("范围 ID 类型无效。")
        matches = tuple(
            row
            for row in state.candidates
            if (row.id == value if field == "world_id" else str(row.id) == value)
        )
        if len(matches) != 1:
            raise QueryResolutionError("范围 ID 不属于当前候选。")
        selected = matches[0]
        frozen = state.values["catalog"].catalog
        if not frozen.available or selected not in frozen.entries:
            raise QueryResolutionError("范围候选不属于本次可信目录。")
        selected_entries = tuple(
            entry
            for entry in state.values["selected_entries"]
            if entry.kind != selected.kind
        ) + (selected,)
        event_refs = state.values["event_refs"] | {event_ref}
        if len(selected_entries) > 2 or len(event_refs) > 3:
            raise QueryResolutionError("范围候选续接已达到边界，请重新查询。")
        values = dict(state.values["parameters"])
        values["server" if field == "world_id" else "dc"] = selected.id
        state.values["selected_entries"] = selected_entries
        state.values["event_refs"] = event_refs
        state.consumed = True
        state.confirmation_ref = event_ref
        return state, values

    def item_event(self, owner, event_ref, generation):
        state = self.get(owner)
        if state is not None and state.values["ticket"].generation == generation:
            if event_ref in state.values["event_refs"]:
                raise QueryResolutionError("范围原始或确认消息不能消费物品候选。")
            return state
        return None

    def selection_catalog(self, state, catalog=None):
        catalog = state.values["catalog"] if catalog is None else catalog
        selected = state.values["selected_entries"]
        if not catalog.catalog.available or any(
            entry not in catalog.catalog.entries for entry in selected
        ):
            raise QueryResolutionError("已选范围不属于当前完整目录。")
        return CatalogSnapshot(
            _SelectedCatalog(catalog.catalog, selected), catalog.provenance
        )

    def current(self, owner, state):
        self.pending.current(owner, state)

    def finish(self, owner, state):
        self.current(owner, state)
        state.consumed = True

    def close(self):
        self.pending.close()
