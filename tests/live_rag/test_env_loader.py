from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.live_rag.env_loader import load_repo_env


class RepoEnvLoaderTests(unittest.TestCase):
    def test_load_repo_env_reads_assignments_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "HF_TOKEN=hf_test_token",
                        "LIVE_RAG_BASE_URL=http://127.0.0.1:9000 # inline comment",
                        "QUOTED_VALUE='hello world'",
                        "",
                    ]
                ),
                encoding="utf-8-sig",
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("HF_TOKEN", None)
                os.environ.pop("LIVE_RAG_BASE_URL", None)
                os.environ.pop("QUOTED_VALUE", None)

                loaded = load_repo_env(env_path)
                self.assertEqual(loaded["HF_TOKEN"], "hf_test_token")
                self.assertEqual(os.environ["HF_TOKEN"], "hf_test_token")
                self.assertEqual(os.environ["LIVE_RAG_BASE_URL"], "http://127.0.0.1:9000")
                self.assertEqual(os.environ["QUOTED_VALUE"], "hello world")

    def test_load_repo_env_does_not_override_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("HF_TOKEN=hf_from_file\n", encoding="utf-8")

            with patch.dict(os.environ, {"HF_TOKEN": "hf_from_environment"}, clear=False):
                loaded = load_repo_env(env_path)

                self.assertEqual(loaded["HF_TOKEN"], "hf_from_environment")
                self.assertEqual(os.environ["HF_TOKEN"], "hf_from_environment")

if __name__ == "__main__":
    unittest.main()
