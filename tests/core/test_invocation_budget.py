"""Origin budgets are Core policy, bounded independently of module identities."""

import unittest
from dataclasses import FrozenInstanceError

from ygl_test_subject.core.invocation_budget import (
    InvocationBudgetPolicy,
    freeze_execution_timeouts,
)

from yomihime_game_link_sdk.contexts import InvocationOrigin


class InvocationBudgetTests(unittest.TestCase):
    def test_default_and_explicit_origins_keep_separate_budgets(self):
        policy = InvocationBudgetPolicy()
        self.assertEqual(policy.seconds(InvocationOrigin.COMMAND, 30), 90)
        self.assertEqual(policy.seconds(InvocationOrigin.LLM_TOOL, 30), 30)
        self.assertEqual(policy.seconds(InvocationOrigin.WEB_PUBLIC, 60), 30)
        self.assertEqual(policy.seconds(InvocationOrigin.WEB_PUBLIC, 7), 7)
        self.assertEqual(policy.seconds(InvocationOrigin.SCHEDULER, 30), 30)
        self.assertEqual(
            InvocationBudgetPolicy(2).seconds(InvocationOrigin.COMMAND, 30), 2
        )

    def test_trusted_deadline_always_wins_and_policy_cannot_be_extended(self):
        policy = InvocationBudgetPolicy()
        self.assertEqual(policy.deadline(InvocationOrigin.COMMAND, 100, 30, 105), 105)
        self.assertEqual(policy.deadline(InvocationOrigin.COMMAND, 100, 30, 500), 190)
        self.assertEqual(policy.deadline(InvocationOrigin.LLM_TOOL, 100, 30, 500), 130)
        for invalid in (0, -1, 91, float("inf"), float("nan"), True, "90"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                InvocationBudgetPolicy(invalid)
        with self.assertRaises(FrozenInstanceError):
            policy.command_timeout = 91

    def test_reviewed_per_id_ceiling_is_copied_and_only_changes_public_target(self):
        key = ("neutral/module", "slow.read")
        supplied = {key: 45}
        policy = InvocationBudgetPolicy(capability_timeouts=supplied)
        supplied[key] = 80
        options = dict(module_id=key[0], capability_id=key[1])
        self.assertEqual(policy.seconds(InvocationOrigin.WEB_PUBLIC, 30, **options), 45)
        self.assertEqual(policy.seconds(InvocationOrigin.COMMAND, 30, **options), 90)
        self.assertEqual(policy.seconds(InvocationOrigin.LLM_TOOL, 30, **options), 30)
        self.assertEqual(policy.seconds(InvocationOrigin.SCHEDULER, 10, **options), 10)
        self.assertEqual(
            policy.seconds(
                InvocationOrigin.WEB_PUBLIC,
                7,
                module_id="other/module",
                capability_id=key[1],
            ),
            7,
        )
        self.assertEqual(policy.seconds(InvocationOrigin.WEB_PUBLIC, 60), 30)
        with self.assertRaises(TypeError):
            policy.capability_timeouts[key] = 80

    def test_reviewed_budget_never_renews_a_shorter_trusted_deadline(self):
        options = dict(module_id="neutral/module", capability_id="slow.read")
        policy = InvocationBudgetPolicy(
            capability_timeouts={("neutral/module", "slow.read"): 45}
        )
        self.assertEqual(
            policy.deadline(InvocationOrigin.WEB_PUBLIC, 100, 30, **options), 145
        )
        self.assertEqual(
            policy.deadline(InvocationOrigin.WEB_PUBLIC, 100, 30, 107, **options), 107
        )
        self.assertEqual(
            policy.deadline(InvocationOrigin.WEB_PUBLIC, 105, 30, 107, **options), 107
        )
        self.assertEqual(
            policy.deadline(InvocationOrigin.WEB_PUBLIC, 100, 30, 100, **options), 100
        )

    def test_execution_mapping_rejects_invalid_values_keys_and_unknown_targets(self):
        target = ("neutral/module", "slow.read")
        for invalid in (
            True,
            False,
            0,
            -1,
            90.01,
            float("nan"),
            float("inf"),
            -float("inf"),
            "45",
            None,
        ):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                freeze_execution_timeouts({target: invalid})
        for invalid in (
            [],
            "policy",
            {"id": 45},
            {("one",): 45},
            {("one", "two", "three"): 45},
            {(True, "two"): 45},
            {("", "two"): 45},
            {("x" * 257, "two"): 45},
        ):
            with self.subTest(mapping=repr(invalid)), self.assertRaises(ValueError):
                freeze_execution_timeouts(invalid)
        with self.assertRaises(ValueError):
            freeze_execution_timeouts({target: 45}, targets={("other/module", "read")})
        with self.assertRaises(ValueError):
            freeze_execution_timeouts({("module", str(i)): 45 for i in range(129)})
        self.assertEqual(dict(freeze_execution_timeouts(None)), {})
        self.assertEqual(
            dict(freeze_execution_timeouts({target: 90}, targets={target})),
            {target: 90},
        )
