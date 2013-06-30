import clang.cindex
import threading
import Queue
from finding import DefinitionFileFinder


class TranslationUnitParsingAction(object):
    def __init__(self, editor, index, translation_units, up_to_date, file):
        self._editor = editor
        self._index = index
        self._translation_units = translation_units
        self._up_to_date = up_to_date
        self._file = file

    def parse(self):
        if self._file_name() in self._translation_units:
            result = self._reuse_existing_translation_unit()
        else:
            result = self._read_new_translation_unit()
        self._up_to_date.add(self._file_name())
        return result

    def _file_name(self):
        return self._file[0]

    def _reuse_existing_translation_unit(self):
        tu = self._translation_units[self._file_name()]
        if self._file_name() not in self._up_to_date:
            tu.reparse([self._file])
        return tu

    def _read_new_translation_unit(self):
        flags = clang.cindex.TranslationUnit.PARSE_PRECOMPILED_PREAMBLE

        args = self._editor.user_options()
        tu = self._index.parse(self._file_name(), args, [self._file], flags)

        if tu is None:
            self._editor.display_message("Cannot parse this source file. The following arguments "
                                         + "are used for clang: " + " ".join(args))
            return None

        self._translation_units[self._file_name()] = tu

        # Reparse to initialize the PCH cache even for auto completion
        # This should be done by index.parse(), however it is not.
        # So we need to reparse ourselves.
        tu.reparse([self._file])
        return tu


class SynchronizedAccess(object):
    def __init__(self):
        self._synchronized_doers = {}
        self._doer_lock = SynchronizedDoer()

    def _synchronized_doer_for_key(self, key):
        def do_it():
            try:
                return self._synchronized_doers[key]
            except KeyError:
                doer = SynchronizedDoer()
                self._synchronized_doers[key] = doer
                return doer
        return self._doer_lock.do(do_it)

    def synchronized_do(self, key, action):
        doer = self._synchronized_doer_for_key(key)
        return doer.do(action)

    def synchronized_if_not_locked_do(self, key, action):
        doer = self._synchronized_doer_for_key(key)
        try:
            return doer.do_if_not_locked(action)
        except AlreadyLocked:
            pass


class SynchronizedTranslationUnitParser(object):
    def __init__(self, index, editor):
        self._editor = editor
        self._index = index
        self._translation_units = dict()
        self._up_to_date = set()
        self._synchronized = SynchronizedAccess()

    def translation_unit_do(self, file_name, get_content, function):
        def do_it():
            return self._call_if_not_null(function, self._parse((file_name, get_content())))
        return self._synchronized.synchronized_do(file_name, do_it)

    def translation_unit_if_parsed_do(self, file, function):
        def do_it():
            if self.is_up_to_date(file[0]):
                return self._call_if_not_null(function, self._parse(file))

        return self._synchronized.synchronized_if_not_locked_do(
                file[0], do_it)

    def _call_if_not_null(self, function, arg):
        if arg:
            return function(arg)

    def _parse(self, file):
        self._editor.display_message("[" + threading.currentThread(
        ).name + " ] - Starting parse: " + file[0])

        action = TranslationUnitParsingAction(self._editor, self._index,
                self._translation_units, self._up_to_date, file)
        result = action.parse()
        self._editor.display_message("[" + threading.currentThread(
        ).name + " ] - Finished parse: " + file[0])
        return result

    def clear_caches(self):
        self._up_to_date = set()

    def is_up_to_date(self, file_name):
        return file_name in self._up_to_date


class IdleTranslationUnitParserThreadDistributor():
    def __init__(self, editor, translation_unit_parser):
        self._editor = editor
        self._remaining_files = Queue.PriorityQueue()
        self._file_contents = {}
        self._parser = translation_unit_parser
        self._threads = [IdleTranslationUnitParserThread(self._editor,
            translation_unit_parser, self._remaining_files, self._file_contents, self.enqueue_file)
            for i in range(1, 8)]
        for thread in self._threads:
            thread.start()

    def terminate(self):
        for thread in self._threads:
            thread.terminate()
        # Only start waking up threads after all threads know they must
        # terminate on notification
        for thread in self._threads:
            self._remaining_files.put((-1, None))

    def enqueue_file(self, file, high_priority=True):
        if self._parser.is_up_to_date(file[0]):
            return

        if high_priority:
            priority = 0
        else:
            priority = 1
        self._file_contents[file[0]] = file[1]
        if (priority, file[0]) not in self._remaining_files.queue:
            self._remaining_files.put((priority, file[0]))


class IdleTranslationUnitParserThread(threading.Thread):
    def __init__(self, editor, translation_unit_parser, _remaining_files, file_contents, enqueue_in_any_thread):
        threading.Thread.__init__(self)
        self._editor = editor
        self._parser = translation_unit_parser
        self._enqueue_in_any_thread = enqueue_in_any_thread
        self._remaining_files = _remaining_files
        self._file_contents = file_contents
        self._termination_requested = False

    def run(self):
        try:
            while True:
                ignored_priority, file_name = self._remaining_files.get()
                if self._termination_requested:
                    return

                def get_contents():
                    return self._file_contents[file_name]
                self._parser.translation_unit_do(file_name, get_contents, lambda tu: tu)
                self._enqueue_definition_files(file_name)
                self._remaining_files.task_done()
        except Exception, e:
            self._editor.display_message(
                "Exception thrown in idle thread: " + str(e))
            raise e

    def terminate(self):
        self._termination_requested = True

    def _enqueue_if_new(self, file_name):
        """Do not enqueue already parsed files to prevent overriding in memory
        files with file-system files."""
        if not file_name in self._file_contents:
            self._enqueue_in_any_thread(get_file_for_file_name(
                file_name), high_priority=False)

    def _enqueue_definition_files(self, file_name):
        finder = DefinitionFileFinder(self._editor.excluded_directories(
        ), file_name)
        for file_name in finder.definition_files():
            self._enqueue_if_new(file_name)


class AlreadyLocked(Exception):
    pass


class SynchronizedDoer(object):
    def __init__(self):
        self._lock = threading.RLock()

    def do(self, action):
        self._lock.acquire()
        try:
            return action()
        finally:
            self._lock.release()

    def do_if_not_locked(self, action):
        if self._lock.acquire(blocking=0):
            try:
                return action()
            finally:
                self._lock.release()
        else:
            raise AlreadyLocked()

    def is_locked(self):
        if self._lock.acquire(blocking=0):
            try:
                return False
            finally:
                self._lock.release()
        else:
            return True


class TranslationUnitAccessor(object):
    def __init__(self, editor):
        self._editor = editor
        self._parser = SynchronizedTranslationUnitParser(clang.cindex.Index.create(), self._editor)
        self._idle_translation_unit_parser_thread_distributor = IdleTranslationUnitParserThreadDistributor(self._editor, self._parser)

    def terminate(self):
        self._idle_translation_unit_parser_thread_distributor.terminate()

    def current_translation_unit_do(self, function):
        current_file = self._editor.current_file()
        return self.translation_unit_do(current_file, function)

    def current_translation_unit_if_parsed_do(self, function):
        current_file = self._editor.current_file()
        return self._parser.translation_unit_if_parsed_do(current_file, function)

    def translation_unit_for_file_named_do(self, file_name, function):
        try:
            file = get_file_for_file_name(file_name)
            return self.translation_unit_do(file, function)
        except IOError:
            return None

    def clear_caches(self):
        self._parser.clear_caches()

    def enqueue_translation_unit_creation(self, file):
        self._idle_translation_unit_parser_thread_distributor.enqueue_file(
            file)

    def translation_unit_do(self, file, function):
        return self._parser.translation_unit_do(file[0], lambda: file[1], function)


def get_file_for_file_name(file_name):
    return (file_name, open(file_name, 'r').read())
