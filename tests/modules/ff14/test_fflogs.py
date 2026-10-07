"""Offline candidate-contract tests for the FFLogs feature adapters."""

from __future__ import annotations

import asyncio
import json
import unittest
from collections import deque
from typing import Any

from modules.ff14.features.fflogs import (
    FFLogsCharacterLookup,
    FFLogsOutputPercentiles,
    parse_statistics_cell,
)
from modules.ff14.features.fflogs_catalog import (
    FFLOGS_CN_SOURCE,
    FFLOGS_GLOBAL_SOURCE,
    MAX_SERVER_PAGES,
    OUTPUT_METADATA_INTROSPECTION,
    PAGE_SIZE,
    FFLogsCatalog,
    parse_graphql_envelope,
    parse_output_metadata,
    parse_output_metadata_projection,
    parse_server_page,
    resolve_output_metadata,
)
from modules.ff14.features.fflogs_models import FFLogsPayloadError, RegionRecord
from yomihime_sdk.api.display import TextBlock
from yomihime_sdk.api.results import ErrorCode, ResultStatus
from yomihime_sdk.api.services import (
    ConfigSnapshot,
    HttpRequest,
    HttpResponse,
    SourceHttpError,
)
from yomihime_sdk.api.storage import SecretMetadata, SecretMetadataState, SecretRef


class _Scope:
    def __init__(self, http: "_FakeHttp") -> None:
        self.http = http


class _Scopes:
    def __init__(self, http: "_FakeHttp") -> None:
        self._scope = _Scope(http)

    async def bind(self, invocation):
        del invocation
        return self._scope


class _FakeHttp:
    def __init__(
        self, responses: list[dict[str, Any] | bytes | SourceHttpError]
    ) -> None:
        self.responses = deque(responses)
        self.requests: list[HttpRequest] = []

    async def fetch(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self.responses.popleft()
        if isinstance(response, SourceHttpError):
            raise response
        body = (
            response if isinstance(response, bytes) else json.dumps(response).encode()
        )
        return HttpResponse(200, {"Content-Type": "application/json"}, body)


def _services(http: _FakeHttp, *, configured=True):
    class Config:
        async def current(self):
            aliases = ("credential_fflogs_cn", "credential_fflogs_global")
            return ConfigSnapshot(
                1,
                {},
                tuple(
                    SecretMetadata(
                        alias,
                        SecretRef(
                            "secret_synthetic", "test", "ff14/ff14", alias, "synthetic"
                        ),
                        1,
                        SecretMetadataState.ACTIVE,
                    )
                    for alias in aliases
                )
                if configured
                else (),
            )

    return type("Services", (), {"scopes": _Scopes(http), "config": Config()})()


def _type(kind: str, name: str | None = None, of_type: dict | None = None) -> dict:
    return {"kind": kind, "name": name, "ofType": of_type}


def _list_type(name: str) -> dict:
    return _type(
        "NON_NULL",
        of_type=_type("LIST", of_type=_type("NON_NULL", of_type=_type("OBJECT", name))),
    )


def _field(name: str, type_value: dict, args: list[dict] | None = None) -> dict:
    return {"name": name, "type": type_value, "args": [] if args is None else args}


def _regions(rows: list[dict] | None = None) -> dict:
    return {
        "data": {
            "worldData": {
                "regions": rows
                if rows is not None
                else [
                    {
                        "id": 1,
                        "name": "North America",
                        "compactName": "北美",
                        "slug": "NA",
                    },
                    {"id": 2, "name": "Europe", "compactName": "欧洲", "slug": "EU"},
                    {"id": 3, "name": "Japan", "compactName": "日本", "slug": "JP"},
                    {"id": 4, "name": "国服", "compactName": "国服", "slug": "CN"},
                    {"id": 5, "name": "Oceania", "compactName": "大洋洲", "slug": "OC"},
                    {"id": 6, "name": "Korea", "compactName": "韩国", "slug": "KR"},
                ]
            }
        }
    }


def _server_row(
    server_id: int = 901,
    *,
    name: str = "Cerberus",
    slug: str = "cerberus",
    region: str = "NA",
    subregion: str = "Aether",
) -> dict:
    region_ids = {"NA": 1, "EU": 2, "JP": 3, "CN": 4, "OC": 5, "KR": 6}
    region_names = {
        "NA": "North America",
        "EU": "Europe",
        "JP": "Japan",
        "CN": "国服",
        "OC": "Oceania",
        "KR": "Korea",
    }
    subregion_ids = {"Aether": 8, "Chaos": 9, "Light": 10, "莫古力": 11}
    return {
        "id": server_id,
        "name": name,
        "normalizedName": name,
        "slug": slug,
        "region": {
            "id": region_ids[region],
            "name": region_names[region],
            "slug": region,
        },
        "subregion": {"id": subregion_ids.get(subregion, 12), "name": subregion},
    }


def _server_page(
    rows: list[dict] | None = None,
    *,
    current_page: int = 1,
    has_more_pages: bool = False,
    per_page: int = PAGE_SIZE,
    region: str = "NA",
) -> dict:
    return {
        "data": {
            "worldData": {
                "region": {
                    "servers": {
                        "data": [_server_row(region=region)] if rows is None else rows,
                        "has_more_pages": has_more_pages,
                        "current_page": current_page,
                        "per_page": per_page,
                    }
                }
            }
        }
    }


def _character(
    *,
    hidden: bool = False,
    rankings: object | None = None,
    metric: str = "rdps",
    zone_id: int = 73,
    difficulty: int = 101,
    partition: int = -1,
) -> dict:
    if rankings is None:
        rankings = {
            "metric": metric,
            "zone": zone_id,
            "difficulty": difficulty,
            "partition": partition,
            "size": 8,
            "rankings": [
                {
                    "encounter": {"id": 1032, "name": "Synthetic Trial"},
                    "rankPercent": 93.5,
                    "bestAmount": 12345.6,
                    "totalKills": 80,
                    "bestSpec": "SyntheticSpec",
                }
            ],
        }
    return {
        "data": {
            "characterData": {
                "character": {
                    "id": 42,
                    "canonicalID": 43,
                    "name": "Synthetic Hero",
                    "hidden": hidden,
                    "server": {"name": "Cerberus"},
                    "rankings": rankings,
                }
            }
        }
    }


def _character_responses(
    *,
    character: dict | None = None,
    first_rows: list[dict] | None = None,
    realm: str = "global",
    metric: str = "rdps",
) -> list[dict]:
    region_codes = ("CN",) if realm == "cn" else ("NA", "EU", "JP", "OC")
    pages = [
        _server_page(
            first_rows
            if index == 0 and first_rows is not None
            else ([_server_row(region=code)] if index == 0 else []),
            region=code,
        )
        for index, code in enumerate(region_codes)
    ]
    return [_regions(), *pages, character or _character(metric=metric)]


def _output_schema() -> dict:
    def scalar(name: str) -> dict:
        return _type("SCALAR", name)

    def object_type(name: str) -> dict:
        return _type("OBJECT", name)

    definitions = {
        "queryType": (
            "Query",
            [
                _field("worldData", object_type("WorldData")),
                _field("gameData", object_type("GameData")),
            ],
        ),
        "worldDataType": (
            "WorldData",
            [_field("zones", _list_type("Zone"))],
        ),
        "zoneType": (
            "Zone",
            [
                _field("id", scalar("Int")),
                _field("name", scalar("String")),
                _field("difficulties", _list_type("Difficulty")),
                _field("encounters", _list_type("Encounter")),
            ],
        ),
        "difficultyType": (
            "Difficulty",
            [_field("id", scalar("Int")), _field("name", scalar("String"))],
        ),
        "encounterType": (
            "Encounter",
            [_field("id", scalar("Int")), _field("name", scalar("String"))],
        ),
        "gameDataType": (
            "GameData",
            [_field("classes", _list_type("GameClass"))],
        ),
        "gameClassType": (
            "GameClass",
            [_field("specs", _list_type("GameSpec"))],
        ),
        "gameSpecType": (
            "GameSpec",
            [
                _field("id", scalar("Int")),
                _field("name", scalar("String")),
                _field("slug", scalar("String")),
            ],
        ),
    }
    return {
        "data": {
            alias: {"name": name, "fields": fields}
            for alias, (name, fields) in definitions.items()
        }
    }


def _output_metadata() -> dict:
    return {
        "data": {
            "worldData": {
                "zones": [
                    {
                        "id": 78,
                        "name": "合成高难副本",
                        "difficulties": [{"id": 3, "name": "合成零式"}],
                        "encounters": [{"id": 1032, "name": "合成首领"}],
                    }
                ]
            },
            "gameData": {
                "classes": [
                    {"specs": [{"id": 7, "name": "合成职业", "slug": "SyntheticSpec"}]}
                ]
            },
        }
    }


def _output_responses(*html: bytes) -> list[dict | bytes]:
    return [_output_schema(), _output_metadata(), *html]


class FFLogsContractTests(unittest.TestCase):
    def test_graphql_envelope_rejects_errors_without_echoing_remote_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "query failed"):
            parse_graphql_envelope(
                b'{"data":{},"errors":[{"message":"remote detail includes token"}]}'
            )

    def test_fixed_server_page_requires_live_candidate_shape_and_region_objects(
        self,
    ) -> None:
        directory = RegionRecord(4, "国服", "国服", "CN")
        payload = _server_page(region="CN")["data"]
        page = parse_server_page(
            payload,
            expected_page=1,
            expected_size=PAGE_SIZE,
            directory_region=directory,
        )
        self.assertFalse(page.has_more_pages)
        self.assertEqual(page.servers[0].region, "CN")
        self.assertEqual(page.servers[0].subregion, "Aether")

        bad_page = _server_page(region="CN")["data"]
        bad_page["worldData"]["region"]["servers"].pop("current_page")
        with self.assertRaisesRegex(FFLogsPayloadError, "page index"):
            parse_server_page(
                bad_page,
                expected_page=1,
                expected_size=PAGE_SIZE,
                directory_region=directory,
            )

        wrong_region = _server_page(region="CN")["data"]
        wrong_region["worldData"]["region"]["servers"]["data"][0]["region"] = "CN"
        with self.assertRaisesRegex(FFLogsPayloadError, "region projection"):
            parse_server_page(
                wrong_region,
                expected_page=1,
                expected_size=PAGE_SIZE,
                directory_region=directory,
            )

    def test_output_schema_is_a_fixed_typed_candidate_projection(self) -> None:
        data = _output_schema()["data"]
        self.assertIsNone(parse_output_metadata_projection(data))
        self.assertIn("worldData", OUTPUT_METADATA_INTROSPECTION)
        self.assertIn("GameClass", OUTPUT_METADATA_INTROSPECTION)

        bad = _output_schema()["data"]
        bad["zoneType"]["fields"][0]["type"] = _type("SCALAR", "String")
        with self.assertRaisesRegex(FFLogsPayloadError, "Zone.id"):
            parse_output_metadata_projection(bad)

        required_arg = _output_schema()["data"]
        required_arg["worldDataType"]["fields"][0]["args"] = [
            {
                "name": "expansion_id",
                "defaultValue": None,
                "type": _type("NON_NULL", of_type=_type("SCALAR", "Int")),
            }
        ]
        with self.assertRaisesRegex(FFLogsPayloadError, "unknown arguments"):
            parse_output_metadata_projection(required_arg)

    def test_output_metadata_resolves_exact_human_labels_to_fixed_ids(self) -> None:
        metadata = parse_output_metadata(_output_metadata()["data"])
        resolved = resolve_output_metadata(metadata, "合成首领", "合成零式", "合成职业")
        self.assertTrue(resolved.complete)
        self.assertEqual(len(resolved.matches), 1)
        candidate = resolved.matches[0]
        self.assertEqual(
            (
                candidate.zone_id,
                candidate.encounter_id,
                candidate.difficulty_id,
                candidate.spec_id,
                candidate.spec_slug,
            ),
            (78, 1032, 3, 7, "SyntheticSpec"),
        )
        self.assertEqual(
            len(
                resolve_output_metadata(
                    metadata, "无此首领", "合成零式", "合成职业"
                ).matches
            ),
            0,
        )

    def test_output_metadata_budgets_and_duplicate_ids_fail_closed(self) -> None:
        payload = _output_metadata()["data"]
        payload["worldData"]["zones"][0]["encounters"].append(
            payload["worldData"]["zones"][0]["encounters"][0]
        )
        with self.assertRaisesRegex(FFLogsPayloadError, "repeats an id"):
            parse_output_metadata(payload)

    def test_server_directory_scans_each_region_and_reports_readable_duplicates(
        self,
    ) -> None:
        duplicate = _server_row(902, region="EU", subregion="Light", slug="cerberus-eu")
        http = _FakeHttp(
            [
                _regions(),
                _server_page(region="NA"),
                _server_page([duplicate], region="EU"),
                _server_page([], region="JP"),
                _server_page([], region="OC"),
            ]
        )
        result = asyncio.run(
            FFLogsCatalog(http, FFLOGS_GLOBAL_SOURCE).resolve_server(
                "global", "cerberus"
            )
        )
        self.assertTrue(result.complete)
        self.assertEqual(len(result.matches), 2)
        self.assertEqual(
            [
                (item.directory_region_name, item.region, item.subregion)
                for item in result.matches
            ],
            [("North America", "NA", "Aether"), ("Europe", "EU", "Light")],
        )
        requests = [json.loads(request.body) for request in http.requests]
        self.assertEqual(requests[1]["variables"]["regionId"], 1)
        self.assertEqual(requests[2]["variables"]["regionId"], 2)
        self.assertNotIn("__type", requests[1]["query"])
        self.assertIn("region { id name slug }", requests[1]["query"])
        self.assertTrue(
            all("Authorization" not in request.headers for request in http.requests)
        )

    def test_server_pagination_rejects_repeated_and_inconsistent_pages(self) -> None:
        cn_regions = _regions(
            [{"id": 4, "name": "国服", "compactName": "国服", "slug": "CN"}]
        )
        repeated = _server_page(has_more_pages=True, region="CN")
        repeated_next = _server_page(
            [_server_row(region="CN")], current_page=2, has_more_pages=True, region="CN"
        )
        http = _FakeHttp([cn_regions, repeated, repeated_next])
        result = asyncio.run(
            FFLogsCatalog(http, FFLOGS_GLOBAL_SOURCE).resolve_server("cn", "Cerberus")
        )
        self.assertFalse(result.complete)
        self.assertEqual(result.matches, ())

        inconsistent = _server_page(current_page=2, region="CN")
        bad_http = _FakeHttp([cn_regions, inconsistent])
        with self.assertRaisesRegex(FFLogsPayloadError, "index is inconsistent"):
            asyncio.run(
                FFLogsCatalog(bad_http, FFLOGS_GLOBAL_SOURCE).resolve_server(
                    "cn", "Cerberus"
                )
            )

    def test_server_page_budget_is_global_across_directory(self) -> None:
        one_region = _regions(
            [{"id": 4, "name": "国服", "compactName": "国服", "slug": "CN"}]
        )
        pages = [
            _server_page(
                [
                    _server_row(
                        1000 + page,
                        name=f"Synthetic {page}",
                        slug=f"synthetic-{page}",
                        region="CN",
                    )
                ],
                current_page=page,
                has_more_pages=True,
                region="CN",
            )
            for page in range(1, MAX_SERVER_PAGES + 1)
        ]
        http = _FakeHttp([one_region, *pages])
        result = asyncio.run(
            FFLogsCatalog(http, FFLOGS_GLOBAL_SOURCE).resolve_server(
                "cn", "Synthetic 1"
            )
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(http.requests), MAX_SERVER_PAGES + 1)

    def test_character_projection_is_strict_and_public_only(self) -> None:
        from modules.ff14.features.fflogs_models import parse_public_character

        parsed = parse_public_character(
            _character()["data"]["characterData"]["character"]
        )
        self.assertEqual(parsed.character_id, 42)
        self.assertEqual(parsed.rankings[0].rank_percent, 93.5)
        self.assertEqual(parsed.partition, -1)
        self.assertEqual(parsed.rankings[0].spec, "SyntheticSpec")
        malformed = _character()["data"]["characterData"]["character"]
        malformed["rankings"] = {"encounterRanks": []}
        with self.assertRaises(FFLogsPayloadError):
            parse_public_character(malformed)


class FFLogsHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_credential_metadata_is_neutral_and_never_does_http(self):
        for invalid in (False, True):
            for handler, parameters in (
                (
                    FFLogsCharacterLookup,
                    {
                        "realm": "global",
                        "server": "Cerberus",
                        "character": "Synthetic Hero",
                    },
                ),
                (
                    FFLogsOutputPercentiles,
                    {
                        "realm": "global",
                        "encounter": "合成首领",
                        "difficulty": "合成零式",
                        "job": "合成职业",
                    },
                ),
            ):
                with self.subTest(handler=handler.__name__, invalid_snapshot=invalid):

                    class Config:
                        async def current(self):
                            if invalid:
                                return {"synthetic-private-value": "not-a-snapshot"}
                            raise RuntimeError("synthetic-private-metadata-error")

                    http = _FakeHttp([])
                    services = _services(http)
                    services.config = Config()
                    result = await handler(services).invoke(None, parameters)
                    self.assertIs(result.error.code, ErrorCode.AUTH_REQUIRED)
                    self.assertIn("无法确认", result.error.message)
                    self.assertNotIn("已配置", result.error.message)
                    self.assertNotIn("尚未配置", result.error.message)
                    self.assertNotIn("synthetic-private", result.error.message)
                    self.assertEqual(http.requests, [])

    async def test_public_character_accepts_chinese_realm_and_directory_resolves_names(
        self,
    ) -> None:
        http = _FakeHttp(_character_responses())
        result = await FFLogsCharacterLookup(_services(http)).invoke(
            None,
            {
                "realm": "国际服",
                "server": "Cerberus",
                "character": "Synthetic Hero",
            },
        )
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(http.requests), 6)
        request = http.requests[-1]
        self.assertEqual(
            (request.source_id, request.method, request.path),
            (FFLOGS_GLOBAL_SOURCE, "POST", "/api/v2/client"),
        )
        self.assertEqual(dict(request.headers), {"Content-Type": "application/json"})
        body = json.loads(request.body)
        self.assertEqual(body["variables"]["serverSlug"], "cerberus")
        self.assertEqual(body["variables"]["serverRegion"], "NA")
        self.assertEqual(body["variables"]["metric"], "rdps")
        self.assertNotIn("zoneID", body["query"])
        self.assertIn("hidden", body["query"])
        self.assertIn("zoneRankings", body["query"])
        text = " ".join(
            block.text
            for block in result.document.ordered_blocks
            if isinstance(block, TextBlock)
        )
        self.assertIn("排名百分位 93.5%", text)
        self.assertIn("国际服角色数据尚未完成核验", result.warnings[0])
        self.assertNotIn("partition", text.casefold())
        self.assertNotIn("size", text.casefold())
        self.assertNotIn("bestamount", text.casefold())
        self.assertIn("最佳 rDPS 12,345.6", text)
        self.assertNotIn("1032", text)

    async def test_character_can_use_a_readable_hint_for_duplicate_server_names(
        self,
    ) -> None:
        rows = [
            _server_row(901, subregion="Aether"),
            _server_row(902, subregion="Chaos", slug="cerberus-2"),
        ]
        ambiguous = _FakeHttp(_character_responses(first_rows=rows))
        needs_choice = await FFLogsCharacterLookup(_services(ambiguous)).invoke(
            None,
            {"realm": "global", "server": "Cerberus", "character": "Hero"},
        )
        self.assertIs(needs_choice.status, ResultStatus.NEEDS_SELECTION)
        text = " ".join(
            block.text
            for block in needs_choice.document.ordered_blocks
            if isinstance(block, TextBlock)
        )
        self.assertIn("North America / 北美 / Aether", text)
        self.assertIn("North America / 北美 / Chaos", text)
        self.assertEqual(len(ambiguous.requests), 5)

        selected = _FakeHttp(_character_responses(first_rows=rows))
        result = await FFLogsCharacterLookup(_services(selected)).invoke(
            None,
            {
                "realm": "global",
                "server": "Cerberus",
                "server_hint": "Chaos",
                "character": "Hero",
            },
        )
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(
            json.loads(selected.requests[-1].body)["variables"]["serverSlug"],
            "cerberus-2",
        )

    async def test_character_metric_zone_filters_and_hidden_profiles_are_distinct(
        self,
    ) -> None:
        http = _FakeHttp(
            _character_responses(
                realm="cn",
                character=_character(
                    metric="ndps", zone_id=2, difficulty=3, partition=4
                ),
            )
        )
        result = await FFLogsCharacterLookup(_services(http)).invoke(
            None,
            {
                "realm": "国服",
                "server": "Cerberus",
                "character": "Synthetic Hero",
                "metric": "NDPS",
                "zone_id": 2,
                "difficulty": 3,
                "partition": 4,
            },
        )
        self.assertIs(result.status, ResultStatus.SUCCESS)
        request = http.requests[-1]
        self.assertEqual(request.source_id, FFLOGS_CN_SOURCE)
        body = json.loads(request.body)
        self.assertEqual(
            {
                key: body["variables"][key]
                for key in ("zoneId", "difficulty", "partition")
            },
            {"zoneId": 2, "difficulty": 3, "partition": 4},
        )
        self.assertIn("zoneID: $zoneId", body["query"])

        for selection in ((99, 3, 4), (2, 99, 4), (2, 3, 99)):
            with self.subTest(selection=selection):
                mismatched = _FakeHttp(
                    _character_responses(
                        realm="cn",
                        character=_character(
                            metric="ndps",
                            zone_id=selection[0],
                            difficulty=selection[1],
                            partition=selection[2],
                        ),
                    )
                )
                rejected = await FFLogsCharacterLookup(_services(mismatched)).invoke(
                    None,
                    {
                        "realm": "cn",
                        "server": "Cerberus",
                        "character": "Synthetic Hero",
                        "metric": "NDPS",
                        "zone_id": 2,
                        "difficulty": 3,
                        "partition": 4,
                    },
                )
                self.assertIs(rejected.error.code, ErrorCode.UNPARSED)

        hidden_http = _FakeHttp(_character_responses(character=_character(hidden=True)))
        hidden = await FFLogsCharacterLookup(_services(hidden_http)).invoke(
            None,
            {"realm": "global", "server": "Cerberus", "character": "Hidden Hero"},
        )
        self.assertIs(hidden.error.code, ErrorCode.NOT_PUBLIC)

    async def test_character_errors_keep_missing_graphql_and_http_distinct(
        self,
    ) -> None:
        missing_responses = _character_responses()
        missing_responses[-1] = {"data": {"characterData": {"character": None}}}
        missing = await FFLogsCharacterLookup(
            _services(_FakeHttp(missing_responses))
        ).invoke(
            None,
            {"realm": "global", "server": "Cerberus", "character": "No Such Character"},
        )
        self.assertIs(missing.error.code, ErrorCode.NOT_FOUND)

        mismatch = await FFLogsCharacterLookup(
            _services(
                _FakeHttp(_character_responses(character=_character(metric="ndps")))
            )
        ).invoke(
            None,
            {"realm": "global", "server": "Cerberus", "character": "Hero"},
        )
        self.assertIs(mismatch.error.code, ErrorCode.UNPARSED)

        graphql_responses = _character_responses()
        graphql_responses[-1] = {"data": None, "errors": [{"message": "private text"}]}
        graphql_error = await FFLogsCharacterLookup(
            _services(_FakeHttp(graphql_responses))
        ).invoke(
            None,
            {"realm": "global", "server": "Cerberus", "character": "Hero"},
        )
        self.assertIs(graphql_error.error.code, ErrorCode.UPSTREAM_ERROR)

        rate_responses = _character_responses()
        rate_responses[-1] = SourceHttpError("upstream_error", status_code=429)
        rate_limited = await FFLogsCharacterLookup(
            _services(_FakeHttp(rate_responses))
        ).invoke(
            None,
            {"realm": "global", "server": "Cerberus", "character": "Hero"},
        )
        self.assertIs(rate_limited.error.code, ErrorCode.RATE_LIMITED)

    def test_statistics_parser_requires_one_explicit_primary_cell(self) -> None:
        html = b'<table><tr><td class="main-table-number primary">12,345.60</td></tr></table>'
        self.assertEqual(parse_statistics_cell(html, expected_percentile=95), 12345.6)
        for body, expected_code in (
            (b"<p>99th percentile: 9999.00</p>", "unknown_markup"),
            (b"<title>Human Verification</title><p>12,345.6</p>", "human_verification"),
            (
                b'<td class="main-table-number primary">1000</td><td class="primary main-table-number">2000</td>',
                "unknown_markup",
            ),
        ):
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(FFLogsPayloadError) as caught:
                    parse_statistics_cell(body, expected_percentile=99)
                self.assertEqual(caught.exception.code, expected_code)

    async def test_output_uses_readable_metadata_then_seven_summary_datasets(
        self,
    ) -> None:
        responses = _output_responses(
            *[b'<td class="main-table-number primary">1000.00</td>' for _ in range(7)]
        )
        http = _FakeHttp(responses)
        result = await FFLogsOutputPercentiles(_services(http)).invoke(
            None,
            {
                "realm": "国服",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
                "metric": "NDPS",
                "period": "latest",
            },
        )
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(http.requests), 9)
        self.assertEqual(http.requests[0].source_id, FFLOGS_CN_SOURCE)
        self.assertIn("Zone", json.loads(http.requests[0].body)["query"])
        self.assertEqual(http.requests[1].path, "/api/v2/client")
        stats = http.requests[2:]
        self.assertTrue(all(item.source_id == "fflogs_stats_cn" for item in stats))
        self.assertTrue(all(item.path == "/zone/statistics/78" for item in stats))
        self.assertEqual(
            [dict(item.query)["dataset"] for item in stats],
            ["10", "25", "50", "75", "95", "99", "100"],
        )
        self.assertTrue(
            all(dict(item.query)["spec"] == "SyntheticSpec" for item in stats)
        )
        self.assertTrue(all(dict(item.query)["difficulty"] == "3" for item in stats))
        self.assertTrue(all(dict(item.query)["dpstype"] == "ndps" for item in stats))
        text = " ".join(
            block.text
            for block in result.document.ordered_blocks
            if isinstance(block, TextBlock)
        )
        self.assertIn("合成高难副本 / 合成首领 / 合成零式 / 合成职业", text)
        self.assertIn("统计周期：FFLogs 默认（latest）", text)
        self.assertIn("尚未完成核验", result.warnings[0])

    async def test_output_defaults_to_latest_and_rejects_unknown_period_without_io(
        self,
    ) -> None:
        success = _FakeHttp(
            _output_responses(
                *[b'<td class="main-table-number primary">42</td>' for _ in range(7)]
            )
        )
        result = await FFLogsOutputPercentiles(_services(success)).invoke(
            None,
            {
                "realm": "global",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
                "metric": "rDPS",
            },
        )
        self.assertIs(result.status, ResultStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(success.requests), 9)
        self.assertNotIn("dpstype", dict(success.requests[2].query))

        invalid = _FakeHttp([])
        bad_result = await FFLogsOutputPercentiles(_services(invalid)).invoke(
            None,
            {
                "realm": "global",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
                "period": "last week",
            },
        )
        self.assertIs(bad_result.error.code, ErrorCode.PARAMETER_ERROR)
        self.assertEqual(invalid.requests, [])

    async def test_output_unknown_schema_or_ambiguous_metadata_never_selects_first(
        self,
    ) -> None:
        malformed = _output_schema()
        malformed["data"]["zoneType"]["fields"][0]["type"] = _type("SCALAR", "String")
        schema_http = _FakeHttp([malformed])
        schema_result = await FFLogsOutputPercentiles(_services(schema_http)).invoke(
            None,
            {
                "realm": "global",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
            },
        )
        self.assertIs(schema_result.error.code, ErrorCode.UNPARSED)
        self.assertEqual(len(schema_http.requests), 1)

        duplicated = _output_metadata()
        duplicate_zone = json.loads(
            json.dumps(duplicated["data"]["worldData"]["zones"][0])
        )
        duplicate_zone["id"] = 79
        duplicate_zone["name"] = "合成高难副本 (duplicate source row)"
        duplicated["data"]["worldData"]["zones"].append(duplicate_zone)
        ambiguous_http = _FakeHttp([_output_schema(), duplicated])
        ambiguous = await FFLogsOutputPercentiles(_services(ambiguous_http)).invoke(
            None,
            {
                "realm": "global",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
            },
        )
        self.assertIs(ambiguous.status, ResultStatus.NEEDS_SELECTION)
        self.assertEqual(len(ambiguous_http.requests), 2)

    async def test_metadata_auth_errors_and_stats_failures_are_bounded(self) -> None:
        auth = _FakeHttp([SourceHttpError("credentials_unavailable")])
        auth_result = await FFLogsOutputPercentiles(_services(auth)).invoke(
            None,
            {
                "realm": "global",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
            },
        )
        self.assertIs(auth_result.error.code, ErrorCode.AUTH_REQUIRED)
        self.assertNotIn("Authorization", auth.requests[0].headers)

        missing = _FakeHttp([])
        absent = await FFLogsOutputPercentiles(
            _services(missing, configured=False)
        ).invoke(
            None,
            {
                "realm": "global",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
            },
        )
        self.assertIs(absent.error.code, ErrorCode.AUTH_REQUIRED)
        self.assertIn("尚未配置", absent.error.message)
        self.assertEqual(missing.requests, [])
        self.assertIn("已配置但", auth_result.error.message)

        challenge = _FakeHttp(_output_responses(b"<title>Human Verification</title>"))
        challenge_result = await FFLogsOutputPercentiles(_services(challenge)).invoke(
            None,
            {
                "realm": "global",
                "encounter": "合成首领",
                "difficulty": "合成零式",
                "job": "合成职业",
            },
        )
        self.assertIs(challenge_result.error.code, ErrorCode.UNPARSED)
        self.assertEqual(len(challenge.requests), 3)

        forbidden = _FakeHttp(
            [
                _output_schema(),
                _output_metadata(),
                SourceHttpError("upstream_error", status_code=403),
            ]
        )
        inaccessible = await FFLogsOutputPercentiles(_services(forbidden)).invoke(
            None,
            {
                "realm": "global",
                "encounter": "\u5408\u6210\u9996\u9886",
                "difficulty": "\u5408\u6210\u96f6\u5f0f",
                "job": "\u5408\u6210\u804c\u4e1a",
            },
        )
        self.assertIs(inaccessible.error.code, ErrorCode.UPSTREAM_ERROR)
        self.assertIn("暂不可访问", inaccessible.error.message)


if __name__ == "__main__":
    unittest.main()
