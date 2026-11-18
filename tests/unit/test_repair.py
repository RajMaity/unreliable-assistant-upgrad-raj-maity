import pytest

from contracts.repair import repair_lot, repair_sku, repair_tenant_id


@pytest.mark.parametrize(
    "raw,expected,rule",
    [
        ("SKU-099", "SKU-099", None),  # already canonical: no rule applied
        ("SKU99", "SKU-099", "R1"),
        ("sku 99", "SKU-099", "R1"),
        ("sku_7", "SKU-007", "R1"),
        ("SKU-0099", "SKU-099", "R1"),  # leading zeros canonicalised
        ("  SKU-099  ", "SKU-099", "R3"),
        ("\u200bSKU-099", "SKU-099", "R3"),  # zero-width space stripped
        ("ＳＫＵ－０９９", "SKU-099", "R3"),  # full-width SKU-099
    ],
)
def test_sku_repairs(raw: str, expected: str, rule: str | None) -> None:
    result = repair_sku(raw)
    assert result is not None
    assert result.value == expected
    assert result.rule == rule


@pytest.mark.parametrize(
    "raw",
    ["SKU-1234", "SKU", "SKU-", "ЅKU-099", "SKU-12a", "", "SKU-١٢٣"],
)
def test_sku_unrepairable(raw: str) -> None:
    assert repair_sku(raw) is None


@pytest.mark.parametrize(
    "raw,expected,rule",
    [
        ("LOT-88392", "LOT-88392", None),
        ("lot 44", "LOT-00044", "R2"),
        ("lot#44", "LOT-00044", "R2"),
        ("LOT:00044", "LOT-00044", "R2"),
        ("lot_88392", "LOT-88392", "R2"),
    ],
)
def test_lot_repairs(raw: str, expected: str, rule: str | None) -> None:
    result = repair_lot(raw)
    assert result is not None
    assert result.value == expected
    assert result.rule == rule


@pytest.mark.parametrize("raw", ["LOT-123456", "lot 1234567", "LOT-ab", "ＬＯＴ-１２３４５６"])
def test_lot_unrepairable(raw: str) -> None:
    assert repair_lot(raw) is None


@pytest.mark.parametrize(
    "raw,expected,rule",
    [
        ("tenant_7", "tenant_7", None),
        ("Tenant_7", "tenant_7", "R4"),
        ("TENANT_abc", "tenant_abc", "R4"),
    ],
)
def test_tenant_repairs(raw: str, expected: str, rule: str | None) -> None:
    result = repair_tenant_id(raw)
    assert result is not None
    assert result.value == expected
    assert result.rule == rule


@pytest.mark.parametrize("raw", ["tenant-7", "acme_7", "tenant_", "tenant_7 x"])
def test_tenant_unrepairable_never_changes_tenant(raw: str) -> None:
    assert repair_tenant_id(raw) is None
