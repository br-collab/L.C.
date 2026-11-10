# Changelog

## 0.2.8 - 9 Nov 2026

Load synthetic account-status trust anchors from checksum-pinned package configuration and
refuse unpinned, revoked, expired, and wrong-producer signatures by name.

## 0.2.7 - 9 Nov 2026

Verify Atreides account status with producer-bound Ed25519 signatures, require explicit
recommendation state for natural persons, and prevent generic lifecycle transitions from
changing sealed order terms.

## 0.2.6 - 9 Nov 2026

Resolve account type from Atreides status evidence and run the recommendation profile gate on
intent acceptance, parent splitting, and order replacement.

## 0.2.5 - 9 Nov 2026

Gate recommended synthetic retail orders on current complete customer profile evidence.

## 0.2.4 - 7 Nov 2026

Pass attested account status through cancel and replace controls.

## 0.2.3 - 7 Nov 2026

Resolve pinned sibling packages in a required strict typing job.

## 0.2.2 - 7 Nov 2026

Allow builders to enable check-gated auto-merge while preserving Bill's sole release authority.

## 0.2.1 - 7 Nov 2026

Clarify merge authority. This documentation release changes no runtime behavior.

## 0.2.0 - 7 Nov 2026

Read Atreides' attested account-status document at market access and refuse every order whose
account is not ACTIVE, or whose status evidence is missing, stale, unreadable, or tampered.
