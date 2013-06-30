class EmacsInterface(object):

    def __init__(self):
        from Pymacs import lisp as emacs
        self._emacs = emacs

    def current_file(self):
        return (self.file_name(), self._emacs.buffer_string())

    def file_name(self):
        return self._emacs.buffer_file_name()

    def user_options(self):
        return ""

    def open_file(self, file_name, line, column):
        self._emacs.find_file(file_name)
        self._emacs.goto_line(line)
        self._emacs.move_to_column(column - 1)

    def debug_enabled(self):
        return False

    def current_line(self):
        return self._emacs.line_number_at_pos()

    def current_column(self):
        return 1 + self._emacs.current_column()

    def display_message(self, message):
        self._emacs.minibuffer_message(message)
