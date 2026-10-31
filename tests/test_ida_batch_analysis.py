import pytest

from pc.ida_batch_analysis import constant_result, format_field_arguments, literal_index, returned_literal, native_declaration, native_type


def test_native_home_metadata_usage_decodes_to_its_literal_index():
    assert literal_index(0xA000A1DB) == 20717


@pytest.mark.parametrize("value", [True, 0, 0xA000A1DA, 0x20024ED7, 0x1A000A1DB, -1])
def test_non_string_or_resolved_pointers_cannot_be_used_as_string_indices(value):
    with pytest.raises(ValueError):
        literal_index(value)


@pytest.mark.parametrize("code,expected", [("return 0;", 0), ("return 1LL;", 1), ("return 0x2;", 2)])
def test_method_constants_preserve_native_enum_values(code, expected):
    assert constant_result(code) == expected


@pytest.mark.parametrize("code", ["return a1;", "if (x) return 0; return 1;", "return qword_1234;", "return 1 + 1;"])
def test_conditional_or_computed_methods_require_further_analysis(code):
    with pytest.raises(ValueError):
        constant_result(code)


def test_route_literal_requires_one_unconditional_return_value():
    assert returned_literal("if (guard == 0) init(); return qword_188449870;") == 0x188449870
    with pytest.raises(ValueError):
        returned_literal("if (x) return qword_1234; return qword_5678;")


def test_format_parameters_follow_boxing_and_aliases_instead_of_read_order():
    code = """
      v6 = *(_DWORD *)(a1 + 16);
      v2 = j_il2cpp_value_box(a1: qword_1000, a2: &v6);
      v7 = *(_DWORD *)(a1 + 20);
      v3 = v2;
      v4 = j_il2cpp_value_box(a1: qword_1000, a2: &v7);
      return sub_183C2FB00(a1: qword_2000, a2: v4, a3: v3, a4: 0);
    """
    assert format_field_arguments(code, 2) == [20, 16]


def test_floating_point_declaration_preserves_static_abi_and_method_info():
    method = {"static": True, "return_type": "System.Single", "parameters": [{"type": "System.Single"}, {"type": "System.Single"}]}
    assert native_declaration(method, {}) == "float __fastcall native_function(float a1, float a2, const void * a3);"


def test_instance_this_and_enum_underlying_type_are_explicit():
    shapes = {"Element": {"kind": "enum", "underlying": "System.Int32"}}
    method = {"static": False, "return_type": "System.Void", "parameters": [{"type": "Result&"}, {"type": "Element"}]}
    assert native_declaration(method, shapes) == "void __fastcall native_function(void * a1, void * a2, int a3, const void * a4);"


@pytest.mark.parametrize("name", ["Result", "T", "System.Span`1<T>"])
def test_unverified_value_and_generic_abi_are_not_guessed(name):
    with pytest.raises(ValueError):
        native_type(name, {"Result": {"kind": "value"}})


def test_constructed_reference_type_uses_pointer_but_generic_value_does_not():
    shapes = {"Register`1": {"kind": "reference"}, "Span`1": {"kind": "value"}}
    assert native_type("Register`1<Unit>", shapes) == "void *"
    with pytest.raises(ValueError):
        native_type("Span`1<Unit>", shapes)
    with pytest.raises(ValueError):
        native_type("Register`1<Unit>/Enumerator", shapes)
