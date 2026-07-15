import time

import requests
from disco.bot import Plugin

from PunyBot import CONFIG
from PunyBot.models import DystopiaBuildCache

# Posts finished dystopia-build CI runs to the builds channel. Shape per hub decision
# 2026-07-15-build-posts-use-punybot-not-a-webhook.md: the BOT polls Forgejo with a repo-read
# token; the CI runners stay credential-free (no webhook, no bot token on CT105/winbuild).
#
# Message format per Mike (deliberately terse, nothing internal):
#   Dystopia build 29-win: succeeded - Steam BuildID: client 24229135, dedicated server 24229143
# The BuildIDs come from SUMMARY.txt on the run's ci-logs/<job> branch (publish-logs writes
# machine-readable `steam_buildid_<appid>:` lines); players/server owners verify against them.

# Job name -> the short suffix shown in the post.
JOB_SUFFIX = {"windows": "win", "linux": "linux"}

# Steam app ids -> the human label in the post, in display order.
BUILDID_LABELS = (("17580", "client"), ("17585", "dedicated server"))

# Only these final states are announced. Cancelled runs are superseded noise
# (concurrency used to kill them; now only a human cancelling does).
POSTED_STATUSES = {"success", "failure"}

POST_SPACING_SECONDS = 0.4


class DystopiaBuildPlugin(Plugin):
    """Announces finished `dystopia-build` Forgejo Actions runs.

    Durable the same way the feed poller is: the largest already-posted task id is stored in
    ``DystopiaBuildCache``, so restarts never re-post. On a TRUE first run the cursor initializes
    to the newest existing task id and posts nothing - history is not backfilled (a builds channel
    only cares about builds from now on).
    """

    def load(self, ctx):
        self._polling = False
        cfg = CONFIG.dystopia_build
        if not cfg or not cfg.channel_id or not cfg.token:
            self.log.info("Dystopia build poller config missing (channel_id/token), skipping.")
        else:
            self.forgejo_url = cfg.forgejo_url.rstrip("/")
            self.repo = cfg.repo
            self.cache_key = f"{self.forgejo_url}#{self.repo}"
            self.log.info("Dystopia build poller starting: %s (%s) every %ss",
                          self.repo, self.forgejo_url, cfg.poll_seconds or 60)
            self.register_schedule(self.poll_builds, cfg.poll_seconds or 60)
        super(DystopiaBuildPlugin, self).load(ctx)

    # -- helpers ---------------------------------------------------------------------------------

    def _api(self, path, timeout=20):
        return requests.get(
            f"{self.forgejo_url}/api/v1/{path}",
            headers={"Authorization": f"token {CONFIG.dystopia_build.token}"},
            timeout=timeout,
        )

    def _buildids_for(self, job_name, run_number):
        """BuildID string for a successful run, from ci-logs/<job>'s SUMMARY.txt - or None.

        The ci-logs branch only ever holds the LATEST run of that job, so the summary's `run:` line
        must match this task's run_number; a mismatch (an even newer run already published) means we
        can't attribute BuildIDs and the post goes out without them rather than with wrong ones.
        """
        try:
            r = self._api(f"repos/{self.repo}/raw/SUMMARY.txt?ref=ci-logs/{job_name}")
            if r.status_code != 200:
                return None
            fields = {}
            for line in r.text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fields[k.strip().lstrip("﻿")] = v.strip()
            if fields.get("run") != str(run_number):
                return None
            ids = [f"{label} {fields['steam_buildid_' + app]}"
                   for app, label in BUILDID_LABELS if fields.get("steam_buildid_" + app)]
            return ", ".join(ids) if ids else None
        except requests.RequestException:
            return None

    def _format(self, task):
        suffix = JOB_SUFFIX.get(task.get("name"), task.get("name") or "?")
        status = "succeeded" if task["status"] == "success" else "failed"
        msg = f"Dystopia build {task['run_number']}-{suffix}: {status}"
        if task["status"] == "success":
            ids = self._buildids_for(task.get("name"), task["run_number"])
            if ids:
                msg += f" - Steam BuildID: {ids}"
        return msg

    # -- poller ----------------------------------------------------------------------------------

    def poll_builds(self):
        if self._polling:
            return
        self._polling = True
        try:
            self._poll_once()
        except Exception:
            self.log.exception("[dystopia_build] poll failed (will retry next tick)")
        finally:
            self._polling = False

    def _poll_once(self):
        r = self._api(f"repos/{self.repo}/actions/tasks?limit=50")
        r.raise_for_status()
        tasks = (r.json() or {}).get("workflow_runs") or []
        if not tasks:
            return

        row = DystopiaBuildCache.get_or_none(DystopiaBuildCache.repo == self.cache_key)
        if row is None:
            # First run: start at the newest task, announce nothing historical.
            top = max(t["id"] for t in tasks)
            DystopiaBuildCache.create(repo=self.cache_key, last_task_id=top)
            self.log.info("[dystopia_build] first run: cursor initialized at task %s (no backfill)", top)
            return

        # Walk strictly upward in id order and stop at the first task that isn't final yet - a
        # still-running build stays above the cursor and gets announced when it finishes, and
        # nothing can be skipped past it. Cancelled tasks are final but silent.
        FINAL = POSTED_STATUSES | {"cancelled"}
        high = row.last_task_id
        for task in sorted((t for t in tasks if t["id"] > row.last_task_id), key=lambda t: t["id"]):
            if task.get("status") not in FINAL:
                break
            if task["status"] in POSTED_STATUSES:
                try:
                    self.bot.client.api.channels_messages_create(
                        CONFIG.dystopia_build.channel_id, content=self._format(task))
                except Exception:
                    self.log.exception("[dystopia_build] post failed for task %s; retrying next tick", task["id"])
                    break
                time.sleep(POST_SPACING_SECONDS)
            high = task["id"]

        if high != row.last_task_id:
            DystopiaBuildCache.update(last_task_id=high).where(
                DystopiaBuildCache.repo == self.cache_key).execute()
