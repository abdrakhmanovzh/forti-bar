#!/usr/bin/env python3
"""Build the ordinary RPM spec using rpmbuild, or its installed native library.

The library route is useful on Bazzite images containing rpm-build-libs but not
the rpmbuild command. All build roots and temporary files stay in this project.
It does not install anything or access the host package database.
"""

import ctypes as C
import ctypes.util
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    source = Path(__file__).resolve().parent.parent
    subprocess.run([str(source / "scripts/package.sh")], check=True)
    work = source / "build/rpm"
    work.mkdir(parents=True, exist_ok=True)
    definitions = {
        "_topdir": str(work), "_sourcedir": str(source / "dist"),
        "_rpmdir": str(source / "dist"), "_srcrpmdir": str(source / "dist"),
        "_tmppath": str(work / "tmp"), "_buildhost": "forti-bar-builder",
    }
    Path(definitions["_tmppath"]).mkdir(exist_ok=True)
    spec = source / "packaging/forti-bar.spec"
    if shutil.which("rpmbuild"):
        args = ["rpmbuild", "-bb", str(spec)]
        for key, value in definitions.items():
            args += ["--define", key + " " + value]
        subprocess.run(args, check=True)
        return 0
    try:
        import rpm
    except ImportError:
        sys.exit("Install rpm-build, or run this script in a Fedora development container.")
    if int(rpm.__version__.split(".")[0]) < 6:
        sys.exit("The library fallback needs RPM 6. Use rpmbuild on older systems.")
    library_path = ctypes.util.find_library("rpmbuild")
    if not library_path:
        sys.exit("Install rpm-build or rpm-build-libs first.")
    for key, value in definitions.items():
        rpm.addMacro(key, value)
    # The image lacks RPM's optional build-root postprocessing scripts. There
    # are no compiled files or packaged manpages; preserve the checked sources.
    rpm.addMacro("__spec_install_post", "%{nil}")
    rpm.addMacro("__check_files", "%{nil}")
    rpm.addMacro("_build_id_links", "none")
    rpm.addMacro("__debug_install_post", "%{nil}")
    rpm.addMacro("__os_install_post", "%{nil}")
    rpm.addMacro("__spec_clean_body", "%{nil}")
    rpm.addMacro("_unpackaged_files_terminate_build", "1")
    rpm_library = C.CDLL(ctypes.util.find_library("rpm"))
    library = C.CDLL(library_path)
    library.rpmSpecParse.argtypes = [C.c_char_p, C.c_uint32, C.c_char_p]
    library.rpmSpecParse.restype = C.c_void_p
    library.rpmSpecFree.argtypes = [C.c_void_p]
    library.rpmSpecFree.restype = C.c_void_p
    rpm_library.rpmtsCreate.restype = C.c_void_p
    rpm_library.rpmtsFree.argtypes = [C.c_void_p]
    rpm_library.rpmtsFree.restype = C.c_void_p

    class BuildArguments(C.Structure):
        _fields_ = [("pkgFlags", C.c_uint32), ("buildAmount", C.c_uint32),
                    ("buildRootOverride", C.c_char_p), ("cookie", C.c_char_p),
                    ("rootdir", C.c_char_p)]

    library.rpmSpecBuild.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(BuildArguments)]
    library.rpmSpecBuild.restype = C.c_int
    # Binary builds finalize after generated spec parts are read, as rpmbuild
    # itself does; finalizing twice creates duplicate header tags.
    parsed = library.rpmSpecParse(str(spec).encode(), rpm.RPMSPEC_ANYARCH | (1 << 4), None)
    if not parsed:
        return 1
    transaction = rpm_library.rpmtsCreate()
    flags = (1 << 23) | rpm.RPMBUILD_PREP | rpm.RPMBUILD_BUILD | rpm.RPMBUILD_INSTALL | rpm.RPMBUILD_CHECK | rpm.RPMBUILD_PACKAGEBINARY
    arguments = BuildArguments(0, flags, None, None, b"/")
    try:
        result = library.rpmSpecBuild(transaction, parsed, C.byref(arguments))
    finally:
        library.rpmSpecFree(parsed)
        rpm_library.rpmtsFree(transaction)
    if result:
        return result
    for package in (source / "dist").rglob("*.rpm"):
        print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
