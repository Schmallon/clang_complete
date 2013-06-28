import os
import Levenshtein
import clang.cindex
from common import get_definition_or_reference


class DeclarationFinder(object):

    def __init__(self, editor, translation_unit_accessor):
        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor

    def _get_current_cursor_in_translation_unit(self, translation_unit):
        location = self._editor.current_location(
        ).clang_location(translation_unit)
        return clang.cindex.Cursor.from_location(translation_unit, location)

    def _find_declaration_in_translation_unit(self, translation_unit):
        current_location_cursor = self._get_current_cursor_in_translation_unit(
            translation_unit)
        parent_cursor = current_location_cursor.semantic_parent
        if not parent_cursor:
            return current_location_cursor.referenced
        for child_cursor in parent_cursor.get_children():
            if child_cursor.canonical == current_location_cursor.canonical:
                return child_cursor
        return current_location_cursor.referenced

    def _declaration_cursors_do(self, function):
        def call_function_with_declaration_in(translation_unit):
            declaration_cursor = self._find_declaration_in_translation_unit(
                translation_unit)
            if declaration_cursor:
                function(declaration_cursor)

        self._translation_unit_accessor.current_translation_unit_do(
            call_function_with_declaration_in)

    def declaration_locations_do(self, function):
        self._declaration_cursors_do(
            lambda cursor: function(cursor.extent.start))


class DefinitionFinder(object):

    def __init__(self, editor, translation_unit_accessor):
        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor

    def _find_corresponding_cursor_in_alternate_translation_unit(self, cursor, other_translation_unit):
        file = cursor.extent.start.file
        for offset in range(cursor.extent.start.offset, cursor.extent.end.offset + 1):
            location = other_translation_unit.get_location(file.name, offset)
            cursor_at_location = clang.cindex.Cursor.from_location(other_translation_unit, location)
            if cursor_at_location.get_usr() == cursor.get_usr():
                return cursor_at_location
        return None

    def _corresponding_cursors_in_any_alternate_translation_unit_do(self, cursor, function):
        def call_function_with_alternate_cursor(translation_unit):
            alternate_cursor = self._find_corresponding_cursor_in_alternate_translation_unit(cursor, translation_unit)
            if alternate_cursor:
                function(alternate_cursor)
        for file_name in self._alternate_files(cursor.extent.start.file.name):
            self._translation_unit_accessor.translation_unit_for_file_named_do(
                file_name, call_function_with_alternate_cursor)

    def _find_definition_in_translation_unit(self, translation_unit, location):
        cursor = clang.cindex.Cursor.from_location(translation_unit, location)
        if cursor.kind.is_unexposed:
            self._editor.display_message("Item at current location is not exposed. Cursor kind: " + str(cursor.kind))
        return get_definition_or_reference(cursor)

    def _definition_or_declaration_cursor_of_current_cursor_in(self, translation_unit):
        current_location = self._editor.current_location(
        ).clang_location(translation_unit)
        return self._find_definition_in_translation_unit(translation_unit, current_location)

    def _alternate_files(self, file_name):
        finder = DefinitionFileFinder(
            self._editor.excluded_directories(), file_name)
        return finder.definition_files()

    def _guessed_alternate_translation_units_do(self, file_name, function):
        for file in self._alternate_files(file_name):
            self._translation_unit_accessor.translation_unit_for_file_named_do(
                file, function)

    def _definitions_of_current_cursor_do(self, translation_unit, function):
        def call_function_with_definition_if_exists(cursor):
            definition = cursor.get_definition()
            if definition:
                function(definition)

        definition_or_declaration_cursor = self._definition_or_declaration_cursor_of_current_cursor_in(translation_unit)
        if definition_or_declaration_cursor:
            if not definition_or_declaration_cursor.is_definition():
                self._corresponding_cursors_in_any_alternate_translation_unit_do(
                    definition_or_declaration_cursor,
                    call_function_with_definition_if_exists)
            function(definition_or_declaration_cursor)

    def _definition_cursors_do(self, function):
        for translation_unit_do in [
            self._translation_unit_accessor.current_translation_unit_do,
            lambda f: self._guessed_alternate_translation_units_do(
                self._editor.file_name(), f),
        ]:
            translation_unit_do(lambda translation_unit: self._definitions_of_current_cursor_do(translation_unit, function))

    def definition_locations_do(self, function):
        self._definition_cursors_do(
            lambda cursor: function(cursor.extent.start))


class DefinitionFileFinder(object):
    """
    Given the name of a file (
        e.g. foo.h), finds similarly named files (e.g. foo.cpp,
    fooI.cpp) somewhere nearby in the file system.
    """
    def __init__(self, excluded_directories, target_file_name):
        self._excluded_directories = excluded_directories
        self._target_file_name = target_file_name
        self._split_target = os.path.splitext(
            os.path.basename(self._target_file_name))
        self._visited_directories = set()
        self._search_limit = 50
        self._num_directories_searched = 0

    def definition_files(self):
        directory_name = os.path.dirname(self._target_file_name)
        for result in self._search_directory_and_parent_directories(directory_name):
            yield result

    def _search_directory_and_parent_directories(self, directory_name):
        for result in self._search_directory_and_subdirectories(directory_name):
            yield result
        parent_directory_name = os.path.abspath(
            os.path.join(directory_name, '..'))
        if parent_directory_name != directory_name:
            for result in self._search_directory_and_parent_directories(parent_directory_name):
                yield result

    def _search_directory_and_subdirectories(self, directory_name):
        self._num_directories_searched = 1 + self._num_directories_searched
        if self._num_directories_searched > self._search_limit:
            return
        self._visited_directories.add(os.path.abspath(directory_name))
        try:
            for file_name in os.listdir(directory_name):
                absolute_name = os.path.abspath(
                    os.path.join(directory_name, file_name))
                if os.path.isdir(absolute_name) and file_name not in self._excluded_directories:
                    if absolute_name not in self._visited_directories:
                        for result in self._search_directory_and_subdirectories(absolute_name):
                            yield result
                else:
                    if self._is_definition_file_name(file_name):
                        yield absolute_name
        except OSError:
            pass

    def _ratio(self, a, b):
        return Levenshtein.ratio(a, b)

    def _is_definition_file_name(self, file_name):
        split_file_name = os.path.splitext(file_name)
        return (self._ratio(split_file_name[0], self._split_target[0]) > 0.8 and
                split_file_name[1] in ('.cpp', 'c'))
