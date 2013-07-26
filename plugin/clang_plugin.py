from common import SingleResultWorker, TickingDispatcher
from completion import Completer
from finding import DeclarationFinder, DefinitionFinder
from highlighting import InterestingRangeHighlighter, export_and_highlight_range_if_in_current_file
from translation_unit_access import TranslationUnitAccessor
import actions
import clang.cindex


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


def make_clang_plugin(editor, clang_complete_flags, library_path):
    if not clang.cindex.Config.loaded:
        if library_path != "":
            clang.cindex.Config.set_library_path(library_path)

        clang.cindex.Config.set_compatibility_check(False)

    translation_unit_accessor = TranslationUnitAccessor(editor)

    return ClangPlugin(editor, translation_unit_accessor, clang_complete_flags)


class CurrentTranslationUnitAccess(object):
    def __init__(self, translation_unit_accessor):
        self._translation_unit_accessor = translation_unit_accessor
        self._listeners = []
        self._worker = SingleResultWorker(self._process)

    def terminate(self):
        self._worker.terminate()

    def file_changed(self, file):
        self._worker.request(file)

    def add_listener(self, listener):
        self._listeners.append(listener)

    def _process(self, file):

        def do_it(translation_unit):
            for listener in self._listeners:
                listener(translation_unit)

        self._translation_unit_accessor.clear_caches()
        self._translation_unit_accessor.translation_unit_do(file, do_it)


class ClangPlugin(object):
    def __init__(self, editor, translation_unit_accessor, clang_complete_flags):

        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor
        self._definition_finder = DefinitionFinder(self._editor, self._translation_unit_accessor)
        self._declaration_finder = DeclarationFinder(self._editor, self._translation_unit_accessor)
        self._completer = Completer(self._editor, self._translation_unit_accessor, int(clang_complete_flags))
        self._current_translation_unit_access = CurrentTranslationUnitAccess(self._translation_unit_accessor)
        self._dispatcher = TickingDispatcher()
        self._interesting_range_highlighter = InterestingRangeHighlighter(self._current_translation_unit_access, self._dispatcher, self._editor)

    def terminate(self):
        self._current_translation_unit_access.terminate()
        self._translation_unit_accessor.terminate()

    def file_changed(self):
        self._editor.display_message("File change was notified, clearing all caches.")
        self._current_translation_unit_access.file_changed(self._editor.current_file())
        self.tick()

    def tick(self):
        self._dispatcher.tick()

    def file_opened(self):
        self._editor.display_message("Noticed opening of new file")
        self._translation_unit_accessor.enqueue_translation_unit_creation(self._editor.current_file())

    def jump_to_definition(self):
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
