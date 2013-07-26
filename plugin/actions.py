from common import get_definition_or_reference
from clang.cindex import CursorKind, TypeKind, TokenKind, SourceRange


def find_diagnostics(translation_unit):
    for diagnostic in translation_unit.diagnostics:
        if diagnostic.severity in (diagnostic.Warning, diagnostic.Error, diagnostic.Note):
            yield SourceRange.from_locations(diagnostic.location, diagnostic.location)
            for range in diagnostic.ranges:
                yield range


def find_implemented_pure_virtual_methods(translation_unit):
    for cursor in cursors_in_file_of_translation_unit(translation_unit):
        if any(filter(lambda c: c.is_pure_virtual_method(), cursor.get_overriden_methods())):
            yield get_identifier_range(cursor)


def find_overriden_method_declarations(translation_unit):
    for cursor in cursors_in_file_of_translation_unit(translation_unit):
        if cursor.is_virtual_method():
            if filter(lambda c: not c.is_pure_virtual_method(), cursor.get_overriden_methods()):
                yield get_identifier_range(cursor)


def find_virtual_method_calls(translation_unit):
    for call_expr in call_expressions_in_file_of_translation_unit(translation_unit):
        cursor_referenced = call_expr.referenced
        if cursor_referenced and cursor_referenced.is_virtual_method():
            yield call_expr.extent


def find_virtual_method_declarations(translation_unit):
    for cursor in cursors_in_file_of_translation_unit(translation_unit):
        if cursor.is_virtual_method():
            yield get_identifier_range(cursor)


def find_non_virtual_methods(translation_unit):
    for cursor in cursors_in_file_of_translation_unit(translation_unit):
        if cursor.kind == CursorKind.CXX_METHOD:
            if not cursor.is_virtual_method():
                yield get_identifier_range(cursor)


def find_static_method_declarations(translation_unit):
    for cursor in cursors_of_kind_in_file_of_translation_unit(translation_unit, CursorKind.CXX_METHOD):
        if cursor.is_static_method():
            yield get_identifier_range(cursor)


def find_member_references(translation_unit):
    for cursor in cursors_in_file_of_translation_unit(translation_unit):
        if cursor.kind == CursorKind.MEMBER_REF_EXPR:
            if cursor.is_implicit_access():
                yield get_identifier_range(cursor)


def find_references(translation_unit):
    for cursor in cursors_in_file_of_translation_unit(translation_unit):
        if cursor.referenced and cursor.referenced.type.kind == TypeKind.LVALUEREFERENCE:
            yield get_identifier_range(cursor)


def find_omitted_default_arguments(translation_unit):

    def _omits_default_argument(cursor):
        """
        This implementation relies on default arguments being represented as
        cursors without extent. This is not ideal and is intended to serve only
        as an intermediate solution.
        """
        for argument in cursor.get_arguments():
            if argument.extent.start.offset == 0 and argument.extent.end.offset == 0:
                return True
        return False

    for call_expr in call_expressions_in_file_of_translation_unit(translation_unit):
        if _omits_default_argument(call_expr):
            yield call_expr.extent


def call_expressions_in_file_of_translation_unit(translation_unit):
    return cursors_of_kind_in_file_of_translation_unit(translation_unit, CursorKind.CALL_EXPR)


def cursors_of_kind_in_file_of_translation_unit(translation_unit, kind):
    return [
        cursor
        for cursor in cursors_in_file_of_translation_unit(translation_unit)
        if cursor.kind == kind]


def dfs(tree, get_children):
    yield tree
    for child in get_children(tree):
        for node in dfs(child, get_children):
            yield node


def cursors_in_file_of_translation_unit(translation_unit):
    top_level_cursors_in_this_file = filter(
        lambda cursor: cursor.location.file and cursor.location.file.name == translation_unit.spelling,
        translation_unit.cursor.get_children())
    for cursor in top_level_cursors_in_this_file:
        for result in dfs(cursor, lambda node: node.get_children()):
            yield result


def make_find_parameters_passed_by_non_const_reference(editor):

    def _get_nonconst_reference_param_indexes(function_decl_cursor):
        result = []
        param_decls = filter(lambda cursor: cursor.kind == CursorKind.PARM_DECL, function_decl_cursor.get_children())
        for index, cursor in enumerate(param_decls):
            if cursor.kind == CursorKind.PARM_DECL:
                if cursor.type.kind in [TypeKind.LVALUEREFERENCE, TypeKind.RVALUEREFERENCE]:
                    if not cursor.type.get_pointee().is_const_qualified():
                        result.append(index)
        return result

    def find_ranges(translation_unit):
        for cursor in call_expressions_in_file_of_translation_unit(translation_unit):
            cursor_referenced = cursor.referenced
            if cursor_referenced:
                args = list(cursor.get_arguments())
                for i in _get_nonconst_reference_param_indexes(cursor_referenced):
                    try:
                        yield args[i].extent
                    except IndexError:
                        editor.display_message("Could not find parameter " + str(i) + " in " + str(cursor.extent))

    return find_ranges


def find_references_to_outside_of_selection(translation_unit, selection_range):

    def location_lt(location1, location2):
        return location1.line < location2.line or (
            location1.line == location2.line and location1.column < location2.column)

    def disjoint_with_selection(cursor):
        return (location_lt(cursor.extent.end, selection_range.start)
                or location_lt(selection_range.end, cursor.extent.start))

    def intersects_with_selection(cursor):
        return not disjoint_with_selection(cursor)

    def do_it(cursor, result):

        class Reference(object):
            def __init__(self, referenced_range, referencing_range):
                self.referenced_range = referenced_range
                self.referencing_range = referencing_range

        referenced_cursor = get_definition_or_reference(cursor)
        if referenced_cursor:
            if not intersects_with_selection(referenced_cursor):
                # Limit the extent to start at the name
                constrained_extent = SourceRange.from_locations(
                    referenced_cursor.location,
                    referenced_cursor.extent.end)
                result.add(Reference(
                           constrained_extent,
                           cursor.extent))

        for child in cursor.get_children():
            if intersects_with_selection(child):
                do_it(child, result)

    result = set()
    do_it(translation_unit.cursor, result)
    return result


def get_identifier_range(cursor):
    for token in cursor.get_tokens():
        if (token.kind == TokenKind.IDENTIFIER
                and token.cursor == cursor):
            return token.extent

    return cursor.extent
