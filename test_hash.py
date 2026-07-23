import json
from openforge.models import RepositoryContext, compute_sha256

r = RepositoryContext(repo="a/b", source_path="/tmp")
hash1 = compute_sha256(r)

# Simulate reading from file
data_dict = r.model_dump(exclude_none=True)
hash2 = "sha256:" + __import__("hashlib").sha256(json.dumps(data_dict, sort_keys=True).encode("utf-8")).hexdigest()

print(hash1)
print(hash2)
print(hash1 == hash2)
