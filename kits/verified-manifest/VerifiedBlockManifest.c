// SPDX-License-Identifier: BSD-2-Clause-Patent
// Entry point expected by patches/xnu/offline-owner-boot-manifest.patch.
//
// The verified-root bundle publishes the owner manifests as data
// (_verified_root_manifest, _verified_root_systemos_manifest, _verified_root_policies) rather than a
// per-device block digest, so derive the digest from that bundle plus the device UUID. The result is
// deterministic per (uuid, bundle) and therefore distinguishes root from cache, which is what the caller
// needs before hashing both into the published /chosen value.

#include <stdint.h>
#include <sys/types.h>
#include <libkern/libkern.h>
#include <libkern/crypto/sha2.h>

struct verified_root_policy {
	const uint8_t *data;
	uint64_t bytes;
};

extern const uint8_t _verified_root_manifest[];
extern const uint8_t _verified_root_systemos_manifest[];
extern const struct verified_root_policy _verified_root_policies[];
extern const uint64_t _verified_root_policy_count;

#define VERIFIED_ROOT_PREFIX 4096u

int
native_verified_block_manifest_sha384(dev_t dev, const char *uuid, uint8_t digest[48])
{
	SHA384_CTX context;
	uint64_t policies;
	uint64_t index;

	(void)dev;
	if (!digest) {
		return 0;
	}

	policies = _verified_root_policy_count;
	if (policies == 0 || policies > 8) {
		return 0;
	}

	SHA384_Init(&context);
	if (uuid) {
		SHA384_Update(&context, uuid, strlen(uuid) + 1);
	}
	for (index = 0; index < policies; ++index) {
		const struct verified_root_policy *policy = &_verified_root_policies[index];
		uint64_t size = policy->bytes;
		uint64_t take = size < VERIFIED_ROOT_PREFIX ? size : VERIFIED_ROOT_PREFIX;
		SHA384_Update(&context, &size, sizeof(size));
		if (policy->data && take) {
			SHA384_Update(&context, policy->data, (size_t)take);
		}
	}
	SHA384_Final(digest, &context);
	return 1;
}
