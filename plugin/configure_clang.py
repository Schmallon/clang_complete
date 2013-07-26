import clang.cindex


def configure():
    clang_path = "/Users/mkl/projects/llvm/ninja/lib"

    if not clang.cindex.Config.library_path:
        clang.cindex.Config.set_library_path(clang_path)
