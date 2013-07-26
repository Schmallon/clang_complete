import clang.cindex


def configure():
    clang_path = open(".clang_path", "r").read().strip()

    if not clang.cindex.Config.library_path:
        clang.cindex.Config.set_library_path(clang_path)
