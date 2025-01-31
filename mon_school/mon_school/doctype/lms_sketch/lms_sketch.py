# -*- coding: utf-8 -*-
# Copyright (c) 2021, FOSS United and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import hashlib
from urllib.parse import urlparse
import frappe
from frappe.model.document import Document
from ... import livecode

DEFAULT_IMAGE = """
<svg viewBox="0 0 300 300" width="300" xmlns="http://www.w3.org/2000/svg">
</svg>
"""

class LMSSketch(Document):
    def before_save(self):
        try:
            is_sketch = self.runtime == "sketch" # old version
            self.svg = livecode.livecode_to_svg(self.code, is_sketch=is_sketch)
        except Exception:
            frappe.log_error(f"Failed to save svg for sketch {self.name}")

    def render_svg(self):
        if self.svg:
            return self.svg

        h = hashlib.md5(self.code.encode('utf-8')).hexdigest()
        cache = frappe.cache()
        key = "sketch-" + h
        value = cache.get(key)
        if value:
            value = value.decode('utf-8')
        else:
            is_sketch = self.runtime == "sketch" # old version
            try:
                value = livecode.livecode_to_svg(self.code, is_sketch=is_sketch)
            except Exception as e:
                print(f"Failed to render {self.name} as svg: {e}")
                pass
            if value:
                cache.set(key, value)
        return value or DEFAULT_IMAGE

    @property
    def sketch_id(self):
        """Returns the numeric part of the name.

        For example, the skech_id will be "123" for sketch with name "SKETCH-123".
        """
        return self.name.replace("SKETCH-", "")

    def get_hash(self):
        """Returns the md5 hash of the code to use for caching.
        """
        return hashlib.md5(self.code.encode("utf-8")).hexdigest()

    def get_image_url(self, mode="s"):
        """Returns the image_url for this sketch.

        The mode argument could be one of "s" (for square)
        or "w" (for wide). The s is the default.
        """
        hash_ = self.get_hash()
        return f"/s/{self.sketch_id}-{hash_}-{mode}.png"

    def get_owner(self):
        """Returns the owner of this sketch as a document.
        """
        return frappe.get_doc("User", self.owner)

    def get_owner_name(self):
        return self.get_owner().full_name

    def get_livecode_url(self):
        doc = frappe.get_cached_doc("LMS Settings")
        return doc.livecode_url

    def get_livecode_ws_url(self):
        url = urlparse(self.get_livecode_url())
        protocol = "wss" if url.scheme == "https" else "ws"
        return protocol + "://" + url.netloc + "/livecode"

    def to_svg(self):
        return self.svg or self.render_svg()

    def render_svg(self):
        h = hashlib.md5(self.code.encode('utf-8')).hexdigest()
        cache = frappe.cache()
        key = "sketch-" + h
        value = cache.get(key)
        if value:
            value = value.decode('utf-8')
        else:
            value = livecode.livecode_to_svg(self.code)
            if value:
                cache.set(key, value)
        return value or DEFAULT_IMAGE

    @staticmethod
    def get_recent_sketches(limit=100, owner=None):
        """Returns the recent sketches.
        """
        filters = {}
        if owner:
            filters = {"owner": owner}
        sketches = frappe.get_all(
            "LMS Sketch",
            filters=filters,
            fields='*',
            order_by='modified desc',
            page_length=limit
        )
        return [frappe.get_doc(doctype='LMS Sketch', **doc) for doc in sketches]

    def __repr__(self):
        return f"<LMSSketch {self.name}>"

@frappe.whitelist()
def save_sketch(name, title, code):
    if not name or name == "new":
        doc = frappe.new_doc('LMS Sketch')
        doc.title = title
        doc.code = code
        doc.runtime = 'python-canvas'
        doc.insert(ignore_permissions=True)
        status = "created"
    else:
        doc = frappe.get_doc("LMS Sketch", name)

        if doc.owner != frappe.session.user:
            return {
                "ok": False,
                "error": "Permission Denied"
            }
        doc.title = title
        doc.code = code
        doc.svg = ''
        doc.save()
        status = "updated"
    return {
        "ok": True,
        "status": status,
        "name": doc.name,
        "id": doc.name.replace("SKETCH-", "")
    }
