"""Public Frappe client facade over transport and focused API resources."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, BinaryIO

from .credentials import CredentialProvider
from .resources import DiscoveryAPI, DocumentsAPI, FilesAPI, MethodsAPI
from .transport import DEFAULT_TIMEOUT, Document, Filters, FrappeTransport

__all__ = ["Document", "Filters", "FrappeClient", "time"]


class FrappeClient:
    def __init__(
        self,
        site: str,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        debug: bool = False,
        read_only: bool = False,
        token_type: str = "token",
        on_unauthorized: Callable[[], str | None] | None = None,
        credential_provider: CredentialProvider | None = None,
    ):
        self._transport = FrappeTransport(
            site,
            token,
            timeout,
            debug=debug,
            read_only=read_only,
            token_type=token_type,
            on_unauthorized=on_unauthorized,
            credential_provider=credential_provider,
        )
        self.site = self._transport.site
        self.debug = debug
        self.read_only = read_only
        self.documents = DocumentsAPI(self._transport)
        self.methods = MethodsAPI(self._transport)
        self.discovery = DiscoveryAPI(self._transport)
        self.files = FilesAPI(self._transport)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "FrappeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._transport.request(method, path, **kwargs)

    def stream_download(
        self,
        path: str,
        writer: Callable[[bytes], object],
        *,
        params: dict[str, Any] | None = None,
    ) -> int:
        return self.files.download(path, writer, params=params)

    def list_documents(
        self, doctype: str, **kwargs: Any
    ) -> tuple[list[Document], bool]:
        return self.documents.list(doctype, **kwargs)

    def get_document(self, doctype: str, name: str) -> Document:
        return self.documents.get(doctype, name)

    def create_document(self, doctype: str, data: Document) -> Document:
        return self.documents.create(doctype, data)

    def update_document(self, doctype: str, name: str, data: Document) -> Document:
        return self.documents.update(doctype, name, data)

    def delete_document(self, doctype: str, name: str) -> Any:
        return self.documents.delete(doctype, name)

    def run_doc_method(
        self, doctype: str, name: str, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        return self.documents.run_method(doctype, name, method, params)

    def get_meta(self, doctype: str) -> Document:
        return self.documents.meta(doctype)

    def get_count(self, doctype: str, filters: Filters | None = None) -> int:
        return self.documents.count(doctype, filters)

    def call_method(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        http_method: str = "POST",
    ) -> Any:
        return self.methods.call(method, params=params, http_method=http_method)

    def get_logged_user(self) -> str:
        return self.methods.logged_user()

    def discovery_root(self) -> Any:
        return self.discovery.root()

    def discovery_search(self, query: str) -> Any:
        return self.discovery.search(query)

    def discovery_list(self) -> Any:
        return self.discovery.list()

    def discovery_show(self, method: str) -> Any:
        return self.discovery.show(method)

    def discovery_doctype_list(self, doctype: str) -> Any:
        return self.discovery.doctype_list(doctype)

    def discovery_doctype_show(self, doctype: str, method: str) -> Any:
        return self.discovery.doctype_show(doctype, method)

    def discovery_supported(self) -> bool:
        return self.discovery.supported()

    def call_document_method(
        self,
        doctype: str,
        name: str,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        http_method: str = "POST",
    ) -> Any:
        return self.methods.call_document(
            doctype, name, method, params=params, http_method=http_method
        )

    def upload_file(
        self,
        fileobj: BinaryIO,
        filename: str,
        *,
        is_private: bool = False,
        doctype: str | None = None,
        docname: str | None = None,
        fieldname: str | None = None,
        folder: str = "Home",
    ) -> Document:
        return self.files.upload(
            fileobj,
            filename,
            is_private=is_private,
            doctype=doctype,
            docname=docname,
            fieldname=fieldname,
            folder=folder,
        )
