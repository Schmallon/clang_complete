from common import ExportedRange
from completion import Completer
from finding import DeclarationFinder, DefinitionFinder
from highlighting import InterestingRangeHighlighter, export_and_highlight_range_if_in_current_file
from translation_unit_access import TranslationUnitAccessor
import actions
import clang.cindex
import sys


"""
Ideas:

    - Highlight methods that don't refer to members (could-be-static)

    - Highlight unused pre-declarations

    - Highlight unused includes (probably not possible)

    - Implement completion and diagnostics for emacs
    For that to work I should first check which parts that are currently
    implemented in vimscript are actually vim specific and vice versa.

    - Allow generic configuration for both vim and emacs

    - Integrate with tags
    For the time that we do not have an index that contains all of a projects
    files, we could make use of tags (as extracted by ctags) to determine which
    files to parse next when looking for definitions.

    - Add a "jump to declaration"
    Often we want to jump to a declaration (e.g. declarations usually a coupled
    with comments). If there was a way to find all declarations referenced by a
    cursor, we could use some heuristics to find the declaration that we want to
    display.

    - Code cleanup
     - There seems to be some confusion between returning NULL or nullCursor
       - get_semantic_parent returns nullCursor
       - get_definition returns NULL
    - Allow jumping through pimpls
    - When opening a new file, right away get possible translation units
     - keep a set of translation unit (name -> translation unit)
      - ensure that accessing this set always uses the most current version of
        the file
     - the current file
     - an alternate file (.h -> .cpp)
     - *not required* referencing translation unit, as we already were there

    - Integrate Jump-To-Definition with tags-based searching
     - Allow finding definitions of commented code
     - Macros
"""


def abort_after_first_call(consumer, producer):
    class ConsumeWasCalled(Exception):
        pass

    def consume_and_abort(x):
        consumer(x)
        raise ConsumeWasCalled
    try:
        producer(consume_and_abort)
    except ConsumeWasCalled:
        pass


def print_cursor_with_children(cursor, n=0):
    sys.stdout.write(n * " ")
    print(str(cursor.kind.name))
    for child in cursor.get_children():
        print_cursor_with_children(child, n + 1)


def make_clang_plugin(editor, clang_complete_flags, library_path):
    if not clang.cindex.Config.loaded:
        if library_path != "":
            clang.cindex.Config.set_library_path(library_path)

        clang.cindex.Config.set_compatibility_check(False)

    translation_unit_accessor = TranslationUnitAccessor(editor)

    return ClangPlugin(editor, translation_unit_accessor, clang_complete_flags)


class ClangPlugin(object):
    def __init__(self, editor, translation_unit_accessor, clang_complete_flags):

        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor
        self._definition_finder = DefinitionFinder(self._editor, self._translation_unit_accessor)
        self._declaration_finder = DeclarationFinder(self._editor, self._translation_unit_accessor)
        self._completer = Completer(self._editor, self._translation_unit_accessor, int(clang_complete_flags))
        self._diagnostics_highlighter = DiagnosticsHighlighter(self._editor)
        self._interesting_range_highlighter = InterestingRangeHighlighter(self._translation_unit_accessor, self._editor)
        self._file_has_changed = True
        self._file_at_last_change = None

    def terminate(self):
        self._translation_unit_accessor.terminate()
        self._interesting_range_highlighter.terminate()

    def _start_rescan(self):
        self._translation_unit_accessor.clear_caches()
        self._load_files_in_background()

    def file_changed(self):
        self._editor.display_message("File change was notified, clearing all caches.")
        self._start_rescan()
        self._file_has_changed = True
        self._file_at_last_change = self._editor.current_file()
        self.tick()

    def tick(self):
        if self._file_has_changed:

            def do_it(translation_unit):
                self._diagnostics_highlighter.highlight_in_translation_unit(
                    translation_unit)

                self._file_has_changed = self._editor.current_file() != self._file_at_last_change

            self._translation_unit_accessor.current_translation_unit_if_parsed_do(do_it)

        self._interesting_range_highlighter.tick()

    def file_opened(self):
        self._editor.display_message("Noticed opening of new file")
        # Why clear on opening, closing is enough.
        self._load_files_in_background()

    def _load_files_in_background(self):
        self._translation_unit_accessor.enqueue_translation_unit_creation(
            self._editor.current_file())

    def jump_to_definition(self):
        #self._editor.user_abortable_perform(
            #functools.partial(abort_after_first_call, self._editor.open_location),
            #self._definition_finder.definition_locations_do)
        abort_after_first_call(self._editor.open_location,
                               self._definition_finder.definition_locations_do)

    def jump_to_declaration(self):
        abort_after_first_call(self._editor.open_location,
                               self._declaration_finder.declaration_locations_do)

    def get_current_completions(self, base):
        "TODO: This must be synchronized as well, but as it runs in a separate thread it gets a bit more complete"
        return self._completer.get_current_completions(base)

    def find_references_to_outside_of_selection(self):
        def do_it(translation_unit):
            return actions.find_references_to_outside_of_selection(
                translation_unit,
                self._editor.selection())
        return self._translation_unit_accessor.current_translation_unit_do(do_it)

    def _export_and_highlight_range_if_in_current_file(self, range, highlight_style):
        export_and_highlight_range_if_in_current_file(self._editor, range, highlight_style)

    def highlight_references_to_outside_of_selection(self):
        references = self.find_references_to_outside_of_selection()

        style_referenced_range = "Referenced Range"
        style_referencing_range = "Referencing Range"
        self._editor.clear_highlights(style_referenced_range)
        self._editor.clear_highlights(style_referencing_range)
        for reference in references:
            self._export_and_highlight_range_if_in_current_file(reference.referenced_range, style_referenced_range)
            self._export_and_highlight_range_if_in_current_file(reference.referencing_range, style_referencing_range)

        qf = [dict({'filename': reference.referenced_range.start.file_name,
                    'lnum': reference.referenced_range.start.line,
                    'col': reference.referenced_range.start.column,
                    'text': 'Reference'}) for reference in references if reference.referenced_range.start.file_name == self._editor.file_name()]

        self._editor.display_diagnostics(qf)


class DiagnosticsHighlighter(object):

    def __init__(self, editor):
        self._editor = editor
        self._highlight_style = "Diagnostic"

    def _highlight_diagnostic(self, diagnostic):

        if diagnostic.severity not in (diagnostic.Warning, diagnostic.Error, diagnostic.Note):
            return

        single_location_range = ExportedRange(
            diagnostic.location, diagnostic.location)
        self._editor.highlight_range(
            single_location_range, self._highlight_style)

        for range in diagnostic.ranges:
            self._editor.highlight_range(range, self._highlight_style)

    def highlight_in_translation_unit(self, translation_unit):
        self._editor.clear_highlights(self._highlight_style)
        map(self._highlight_diagnostic, translation_unit.diagnostics)
