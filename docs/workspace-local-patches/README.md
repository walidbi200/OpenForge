# Local testbed changes

These patches preserve the current uncommitted source changes in independent OSS testbed clones. Apply only to the recorded baseline commit in a separate checkout after review. The original clones and Git histories remain under `workspace/`; no nested `.git` directory was copied. Other testbed clones had no commits absent from their remote refs and no working source diff.

- `docs/workspace-local-patches/fastapi-fastapi.patch`: `/home/zhufan/Downloads/openforge/workspace/fastapi-fastapi`, baseline `704fbe1439341994100622853f515a8af7ccc2eb`.
- `docs/workspace-local-patches/pallets-flask.patch`: `/home/zhufan/Downloads/openforge/workspace/pallets-flask`, baseline `36e4a824f340fdee7ed50937ba8e7f6bc7d17f81`.
