"""Release gates for modes capable of placing real mainnet orders."""

from __future__ import annotations


MAINNET_RELEASE_APPROVED = False


def require_mainnet_release_approval(component: str) -> None:
    """Fail closed until the full execution release checklist is approved.

    This is deliberately a source-controlled gate rather than an environment
    variable: production order access must be enabled by a reviewed release,
    not a deployment-time configuration change.
    """
    if not MAINNET_RELEASE_APPROVED:
        raise RuntimeError(
            f"{component} mainnet execution is disabled pending execution "
            "lifecycle, reconciliation, testnet-smoke, and CI release approval."
        )
