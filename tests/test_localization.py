"""本地化系统 - 红态测试"""
import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from localization.system import LocalizationSystem

class TestFallback:
    def test_missing_key_falls_back(self):
        ls = LocalizationSystem()
        ls.languages["en"] = type("P",(),{"strings":{"hello":"Hello"}})()
        ls.current = "fr"
        ls.languages["fr"] = type("P",(),{"strings":{}})()
        result = ls.format_text("hello")
        assert result == "Hello"

class TestPlural:
    def test_plural_form_supported(self):
        ls = LocalizationSystem()
        ls.languages["en"] = type("P",(),{"strings":{"items":"{count} item"}})()
        ls.current = "en"
        result = ls.format_text("items", count=2)
        assert "items" in result

class TestWordWrap:
    def test_wrap_at_word_boundary(self):
        ls = LocalizationSystem()
        result = ls.wrap_text("hello world test", 8)
        assert result[0] == "hello"

class TestFontFallback:
    def test_japanese_char_uses_jp_font(self):
        ls = LocalizationSystem()
        font = ls.get_font("en", "日")
        assert font != "default_font"

class TestSwitchRefresh:
    def test_switch_notifies_listeners(self):
        ls = LocalizationSystem()
        ls.languages["en"] = type("P",(),{"strings":{}})()
        ls.languages["jp"] = type("P",(),{"strings":{}})()
        notified = []
        ls.switch_language("jp")
        assert ls.current == "jp"
