"""Origin budgets are Core policy, bounded independently of module identities."""

import unittest
from dataclasses import FrozenInstanceError

from ygl_test_subject.core.invocation_budget import InvocationBudgetPolicy

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
