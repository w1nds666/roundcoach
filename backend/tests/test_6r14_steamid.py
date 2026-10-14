"""6R14：steamid 规范化/校验/掩码服务测试。

覆盖：SteamID64 严格 17 位且在合法范围；STEAM_0:X:Y / [U:1:account] / 纯 account 转换；
claimed_id 解析（OpenID identity URL）；掩码不泄漏完整 ID；NaN/超范围/非法拒绝。
"""
import pytest

from app.services.steamid import (
    STEAMID64_MAX,
    STEAMID64_MIN,
    is_valid_steamid64,
    mask_steamid64,
    normalize_to_steamid64,
    parse_claimed_id,
)


class TestSteamIdValidation:
    def test_lower_bound_valid(self):
        assert is_valid_steamid64(str(STEAMID64_MIN)) is True

    def test_upper_bound_valid(self):
        assert is_valid_steamid64(str(STEAMID64_MAX)) is True

    def test_exactly_17_digits(self):
        assert is_valid_steamid64("76561198812054537") is True

    def test_known_steamid64(self):
        # 常见示例
        assert is_valid_steamid64("76561197960265728") is True

    def test_below_min_invalid(self):
        assert is_valid_steamid64(str(STEAMID64_MIN - 1)) is False

    def test_above_max_invalid(self):
        assert is_valid_steamid64(str(STEAMID64_MAX + 1)) is False

    def test_too_short_invalid(self):
        assert is_valid_steamid64("76561198") is False

    def test_too_long_invalid(self):
        assert is_valid_steamid64("765611988120545371") is False

    def test_non_numeric_invalid(self):
        assert is_valid_steamid64("76561198abc") is False

    def test_negative_invalid(self):
        assert is_valid_steamid64("-7656119881205453") is False

    def test_empty_invalid(self):
        assert is_valid_steamid64("") is False

    def test_none_invalid(self):
        assert is_valid_steamid64(None) is False


class TestNormalize:
    def test_passthrough_steamid64(self):
        assert normalize_to_steamid64("76561198812054537") == "76561198812054537"

    def test_steam3_ui(self):
        # [U:1:xxxx] account → SteamID64 = offset + account
        account = 12345
        expected = str(STEAMID64_MIN + account)
        assert normalize_to_steamid64(f"[U:1:{account}]") == expected

    def test_legacy_steam0(self):
        # STEAM_0:X:Y → account = Y*2 + X
        assert normalize_to_steamid64("STEAM_0:1:1234") == str(STEAMID64_MIN + 1234 * 2 + 1)

    def test_legacy_steam_alt_universe(self):
        # STEAM_1:0:999 → account = Y*2 + X = 999*2 + 0 = 1998（universe 不影响 account）
        assert normalize_to_steamid64("STEAM_1:0:999") == str(STEAMID64_MIN + 1998)

    def test_raw_account_id(self):
        assert normalize_to_steamid64("4294967295") == str(STEAMID64_MIN + 4294967295)

    def test_garbage_returns_none(self):
        assert normalize_to_steamid64("not-a-steam") is None

    def test_bounds_account_id_too_big(self):
        # account >= 2^32 超出范围
        assert normalize_to_steamid64("99999999999") is None


class TestMask:
    def test_mask_hides_middle(self):
        masked = mask_steamid64("76561198812054537")
        assert masked.startswith("765611") and masked.endswith("54537")
        assert "8120545" not in masked
        assert masked != "76561198812054537"

    def test_mask_separator(self):
        assert "****" in mask_steamid64("76561198812054537")

    def test_mask_none(self):
        assert mask_steamid64(None) is None

    def test_mask_invalid_still_masked(self):
        # 非法输入不抛错，保守掩码
        assert mask_steamid64("random") is not None


class TestClaimedId:
    def test_parse_valid(self):
        sid = "76561198812054537"
        claimed = f"https://steamcommunity.com/openid/id/{sid}"
        assert parse_claimed_id(claimed) == sid

    def test_parse_with_trailing_slash(self):
        sid = "76561198812054537"
        claimed = f"https://steamcommunity.com/openid/id/{sid}/"
        assert parse_claimed_id(claimed) == sid

    def test_parse_wrong_host_none(self):
        assert parse_claimed_id("https://evil.example.com/openid/id/76561198812054537") is None

    def test_parse_wrong_path_none(self):
        assert parse_claimed_id("https://steamcommunity.com/openid/other/76561198812054537") is None

    def test_parse_not_a_steamid(self):
        assert parse_claimed_id("https://steamcommunity.com/openid/id/not-a-steam") is None

    def test_parse_none(self):
        assert parse_claimed_id(None) is None
        assert parse_claimed_id("") is None
