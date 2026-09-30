"""Registered lifecycle-path profiles, separate from trade economics."""

from __future__ import annotations

from typing import Self

from cannae_kernel.delivery import DeliveryPattern
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "BILATERAL_TREASURY",
    "DEFAULT_ASSET_PROFILES",
    "TOKENIZED_TREASURY_SINGLE_PLATFORM",
    "AssetProfile",
    "AssetProfileRegistry",
]


class AssetProfile(BaseModel):
    """Non-economic facts that select a lifecycle path to finality."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    settlement_pattern: DeliveryPattern
    cash_representation: str = Field(min_length=1)
    custody_path: str = Field(min_length=1)
    conditional_execution: str = Field(min_length=1)
    securities_finality_evidence: str = Field(min_length=1)
    cash_finality_evidence: str = Field(min_length=1)


class AssetProfileRegistry(BaseModel):
    """Immutable data registry; admitting a profile requires no enum change."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    profiles: tuple[AssetProfile, ...]

    @model_validator(mode="after")
    def _profile_ids_are_unique(self) -> Self:
        identifiers = tuple(profile.profile_id for profile in self.profiles)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("asset profile identifiers must be unique")
        return self

    def register(self, profile: AssetProfile) -> AssetProfileRegistry:
        return AssetProfileRegistry(profiles=(*self.profiles, profile))

    def resolve(self, profile_id: str) -> AssetProfile:
        for profile in self.profiles:
            if profile.profile_id == profile_id:
                return profile
        raise KeyError(f"asset profile is not registered: {profile_id}")


BILATERAL_TREASURY = AssetProfile(
    profile_id="treasury.bilateral",
    settlement_pattern=DeliveryPattern.DVP,
    cash_representation="commercial-bank money over Fedwire Funds",
    custody_path="bilateral securities custody",
    conditional_execution="release securities and cash as delivery versus payment",
    securities_finality_evidence="Fedwire Securities transfer finality",
    cash_finality_evidence="Fedwire Funds payment finality",
)

TOKENIZED_TREASURY_SINGLE_PLATFORM = AssetProfile(
    profile_id="treasury.tokenized.single-platform",
    settlement_pattern=DeliveryPattern.DVP,
    cash_representation="tokenized cash on the settlement platform",
    custody_path="single-platform token custody",
    conditional_execution="atomic delivery versus tokenized payment",
    securities_finality_evidence="platform token-transfer finality",
    cash_finality_evidence="platform tokenized-cash finality",
)

DEFAULT_ASSET_PROFILES = AssetProfileRegistry(
    profiles=(BILATERAL_TREASURY, TOKENIZED_TREASURY_SINGLE_PLATFORM)
)
