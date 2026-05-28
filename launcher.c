#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libgen.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    char exec_path[4096];
    uint32_t size = sizeof(exec_path);
    if (_NSGetExecutablePath(exec_path, &size) != 0) {
        fprintf(stderr, "FileGenius: could not resolve executable path\n");
        return 1;
    }

    char *dir = dirname(exec_path);  /* .../MacOS */
    char resources[4096];
    snprintf(resources, sizeof(resources), "%s/../Resources", dir);

    /* Set PYTHONPATH so `python -m filegenius` finds the source */
    char pythonpath[8192];
    const char *existing = getenv("PYTHONPATH");
    if (existing && existing[0]) {
        snprintf(pythonpath, sizeof(pythonpath), "%s:%s", resources, existing);
    } else {
        snprintf(pythonpath, sizeof(pythonpath), "%s", resources);
    }
    setenv("PYTHONPATH", pythonpath, 1);

    /* chdir into the source directory */
    char src_dir[4096];
    snprintf(src_dir, sizeof(src_dir), "%s", resources);
    chdir(src_dir);

    /* Try GUI-capable Python framework first (required for tkinter) */
    const char *pythons[] = {
        "/Library/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python",
        "/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python",
        "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python",
        "/opt/homebrew/bin/python3",
        "/usr/bin/python3",
        NULL
    };

    for (int i = 0; pythons[i] != NULL; i++) {
        if (access(pythons[i], X_OK) == 0) {
            execl(pythons[i], pythons[i], "-m", "filegenius", NULL);
        }
    }

    fprintf(stderr, "FileGenius: Python 3 not found. Please install Python 3 from python.org\n");
    return 1;
}
