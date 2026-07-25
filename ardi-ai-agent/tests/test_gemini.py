import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set a dummy key to avoid client init failure
os.environ["GEMINI_API_KEY"] = "test-key"

from ai.gemini import _parse_json_safely


class TestParseJsonSafely:
    def test_plain_json_object(self):
        result = _parse_json_safely('{"name": "Coke", "price": 45}')
        assert result == {"name": "Coke", "price": 45}

    def test_json_with_trailing_text(self):
        result = _parse_json_safely('Some reply text\n{"name": "Coke"}')
        assert result == {"name": "Coke"}

    def test_json_with_code_fence(self):
        result = _parse_json_safely('```json\n{"name": "Coke"}\n```')
        assert result == {"name": "Coke"}

    def test_json_with_code_fence_no_lang(self):
        result = _parse_json_safely('```\n{"name": "Coke"}\n```')
        assert result == {"name": "Coke"}

    def test_non_dict_json(self):
        result = _parse_json_safely('"items"')
        assert result is None

    def test_list_json(self):
        # _parse_json_safely extracts embedded {} even inside a list
        result = _parse_json_safely('[{"name": "Coke"}]')
        assert result == {"name": "Coke"}

    def test_invalid_json(self):
        result = _parse_json_safely("{bad json}")
        assert result is None

    def test_no_braces(self):
        result = _parse_json_safely("just some text")
        assert result is None

    def test_empty_string(self):
        result = _parse_json_safely("")
        assert result is None

    def test_nested_json(self):
        result = _parse_json_safely('{"items": [{"product": "Coke", "qty": 1}], "name": "Abebe"}')
        assert result == {"items": [{"product": "Coke", "qty": 1}], "name": "Abebe"}

    def test_ard_identity_start(self):
        result = _parse_json_safely(
            'Ardi AI - Ethiopian Business Assistant\n{"intent": "add_product"}'
        )
        assert result == {"intent": "add_product"}
