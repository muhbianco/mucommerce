from __future__ import annotations

from app.tenancy.dns import DnsVerifier, instructions_for


def _fake(records: dict[tuple[str, str], list[str]]):  # type: ignore[no-untyped-def]
    async def resolve(name: str, rdtype: str) -> list[str]:
        return records.get((name, rdtype), [])

    return resolve


async def test_apex_with_txt_and_a_record_is_ok() -> None:
    verifier = DnsVerifier(
        _fake(
            {
                ("_muhbianco-verify.lunares.com.br", "TXT"): ["mb-verify=tok"],
                ("lunares.com.br", "A"): ["203.0.113.10"],
            }
        )
    )
    check = await verifier.check("lunares.com.br", "tok")
    assert check.txt_ok and check.target_ok
    assert check.errors == []


async def test_subdomain_with_cname_is_ok_but_wrong_txt_fails() -> None:
    verifier = DnsVerifier(
        _fake(
            {
                ("_muhbianco-verify.www.lunares.com.br", "TXT"): ["mb-verify=other"],
                ("www.lunares.com.br", "CNAME"): ["edge.test."],
            }
        )
    )
    check = await verifier.check("www.lunares.com.br", "tok")
    assert check.txt_ok is False
    assert check.observed_txt == ["mb-verify=other"]
    assert check.target_ok is True
    assert check.observed_cname == "edge.test"


async def test_wrong_a_record_is_not_ok() -> None:
    verifier = DnsVerifier(_fake({("x.com", "A"): ["198.51.100.1"]}))
    check = await verifier.check("x.com", "tok")
    assert check.target_ok is False


def test_instructions() -> None:
    instr = instructions_for("lunares.com.br", "tok", apex=True)
    assert instr.txt_name == "_muhbianco-verify.lunares.com.br"
    assert instr.txt_value == "mb-verify=tok"
    assert instr.cname_target == "edge.test"
    assert instr.a_records == ["203.0.113.10"]
