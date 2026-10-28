import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_qa_script as qa_examples
from issue_delivery_orchestrator import runtime_recipe
from issue_delivery_orchestrator.config import settings
from issue_delivery_orchestrator.errors import OrchestrationError, RunBlocked


class RuntimeRecipeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = qa_examples.ResultsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture.state
        self.manifest = self.fixture.root / "manifest.json"
        self.manifest.write_text("{}")
        self.state.update({"runtimes": [{"runtimeId": "rt-1", "manifestPath": str(self.manifest)}],
                           "activeRuntimeId": "rt-1", "ownedProcesses": []})
        for target in ("issue_delivery_orchestrator.runtime_recipe.save_state",
                       "issue_delivery_orchestrator.runtime.save_state"):
            saving = patch(target)
            saving.start()
            self.addCleanup(saving.stop)
        polling = patch.object(runtime_recipe, "POLL_SECONDS", 0.05)
        polling.start()
        self.addCleanup(polling.stop)
        self.flags = self.fixture.root / "flags"
        self.flags.mkdir()

    def recipe(self, services):
        path = self.fixture.root / "runtime-recipe.json"
        path.write_text(json.dumps({"services": services}))
        return runtime_recipe.register_recipe(self.state, path)

    def service(self, name, *, start=None, timeout=5):
        flag = self.flags / name
        return {"name": name, "health": f"test -f {flag}",
                "start": start or f"sleep 0.2; echo \"$LOCAL_RUNTIME_MANIFEST\" > {flag}",
                "timeoutSeconds": timeout}

    def test_rejects_invalid_or_secret_bearing_recipes(self):
        for services in ([], [{"name": "web"}], [self.service("db"), self.service("db")],
                         [{**self.service("api"), "timeoutSeconds": 0}],
                         [{**self.service("api"), "start": "JWT_SECRET=supersecretvalue123456 node api"}]):
            with self.subTest(services=services), self.assertRaises(OrchestrationError):
                self.recipe(services)
        outside = Path(self.fixture.temporary.name) / "recipe.json"
        outside.write_text(json.dumps({"services": [self.service("db")]}))
        with self.assertRaises(OrchestrationError):
            runtime_recipe.register_recipe(self.state, outside)

    def test_restore_rebinds_skips_healthy_and_starts_down_services_in_order(self):
        (self.flags / "postgres").write_text("up")
        registered = self.recipe([self.service("postgres"), self.service("api")])
        self.assertEqual(registered["services"], ["postgres", "api"])
        binding = self.fixture.worktree / settings().runtime_root / "worktree.json"
        self.assertFalse(binding.exists())
        with patch("issue_delivery_orchestrator.runtime_recipe.register_owned_process") as owned:
            outcome = runtime_recipe.restore(self.state)
        self.assertTrue(outcome["bindingRestored"])
        self.assertEqual(json.loads(binding.read_text())["manifestPath"], str(self.manifest))
        self.assertEqual([(item["name"], item["status"]) for item in outcome["services"]],
                         [("postgres", "ok"), ("api", "started")])
        self.assertEqual((self.flags / "api").read_text().strip(), str(self.manifest))
        self.assertEqual(outcome["head"], self.fixture.head)
        self.assertEqual(owned.call_args.kwargs["kind"], "runtime:api")
        again = runtime_recipe.restore(self.state)
        self.assertFalse(again["bindingRestored"])
        self.assertEqual({item["status"] for item in again["services"]}, {"ok"})

    def test_failed_service_blocks_with_its_log_tail_and_stops_the_sequence(self):
        self.recipe([self.service("redis", start="echo port 6379 in use; exit 3", timeout=2),
                     self.service("api")])
        with self.assertRaises(RunBlocked) as raised:
            runtime_recipe.restore(self.state)
        self.assertIn("redis: failed", str(raised.exception))
        self.assertIn("port 6379 in use", str(raised.exception))
        self.assertFalse((self.flags / "api").exists())

    def test_restore_requires_a_recipe_and_a_live_manifest(self):
        with self.assertRaisesRegex(RunBlocked, "runtime-recipe"):
            runtime_recipe.restore(self.state)
        self.recipe([self.service("db")])
        self.manifest.unlink()
        with self.assertRaisesRegex(RunBlocked, "runtime-init"):
            runtime_recipe.restore(self.state)
