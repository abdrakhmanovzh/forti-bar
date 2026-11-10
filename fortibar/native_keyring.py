"""Small libsecret binding so a uv environment needs no PyGObject compiler stack."""

import ctypes as C
import ctypes.util


class NativeKeyring:
    def __init__(self):
        self.secret = C.CDLL(ctypes.util.find_library("secret-1") or "libsecret-1.so.0")
        self.gio = C.CDLL(ctypes.util.find_library("gio-2.0") or "libgio-2.0.so.0")
        self.gobject = C.CDLL(ctypes.util.find_library("gobject-2.0") or "libgobject-2.0.so.0")
        self.glib = C.CDLL(ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0")
        pointer, text, error = C.c_void_p, C.c_char_p, C.POINTER(C.c_void_p)
        self.secret.secret_schema_new.argtypes = [text, C.c_int]
        self.secret.secret_schema_new.restype = pointer
        self.secret.secret_schema_unref.argtypes = [pointer]
        self.secret.secret_password_lookup_sync.argtypes = [pointer, pointer, error]
        self.secret.secret_password_lookup_sync.restype = pointer
        self.secret.secret_password_store_sync.argtypes = [pointer, text, text, text, pointer, error]
        self.secret.secret_password_store_sync.restype = C.c_int
        self.secret.secret_password_clear_sync.argtypes = [pointer, pointer, error]
        self.secret.secret_password_clear_sync.restype = C.c_int
        self.secret.secret_password_free.argtypes = [pointer]
        self.gio.g_cancellable_new.restype = pointer
        self.gio.g_cancellable_cancel.argtypes = [pointer]
        self.gobject.g_object_unref.argtypes = [pointer]
        self.glib.g_error_free.argtypes = [pointer]
        self.schema = self.secret.secret_schema_new(
            b"io.github.fortibar.Password", 0, C.c_char_p(b"profile"), C.c_int(0), C.c_void_p())
        self.cancellable = self.gio.g_cancellable_new()
        if not self.schema or not self.cancellable:
            self.close()
            raise RuntimeError("The keyring could not be initialized.")

    def cancel(self):
        if self.cancellable:
            self.gio.g_cancellable_cancel(self.cancellable)

    def execute(self, action, profile_id, value=""):
        error = C.c_void_p()
        attributes = (C.c_char_p(b"profile"), C.c_char_p(profile_id.encode()), C.c_void_p())
        result = ""
        if action == "get":
            pointer = self.secret.secret_password_lookup_sync(self.schema, self.cancellable, C.byref(error), *attributes)
            if pointer:
                try:
                    result = C.string_at(pointer).decode()
                finally:
                    self.secret.secret_password_free(pointer)
        elif action == "set":
            success = self.secret.secret_password_store_sync(
                self.schema, b"default", b"Forti Bar VPN password", value.encode(),
                self.cancellable, C.byref(error), *attributes)
            if not success and not error.value:
                raise RuntimeError("The keyring did not save the password.")
        else:
            self.secret.secret_password_clear_sync(self.schema, self.cancellable, C.byref(error), *attributes)
        if error.value:
            self.glib.g_error_free(error)
            raise RuntimeError("The desktop keyring is unavailable or locked.")
        return result

    def close(self):
        if getattr(self, "schema", None):
            self.secret.secret_schema_unref(self.schema)
            self.schema = None
        if getattr(self, "cancellable", None):
            self.gobject.g_object_unref(self.cancellable)
            self.cancellable = None
