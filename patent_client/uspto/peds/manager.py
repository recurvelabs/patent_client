import datetime
import logging
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import AsyncIterator
from typing import TYPE_CHECKING

from dateutil.parser import parse as dt_parse
from pypdf import PdfMerger

from .api import PatentExaminationDataSystemApi
from .query import QueryFields
from patent_client.util.asyncio_util import run_sync
from patent_client.util.manager import Manager
from patent_client.util.request_util import get_start_and_row_count

if TYPE_CHECKING:
    from .model import USApplication, Document

logger = logging.getLogger(__name__)


class HttpException(Exception):
    pass


def cast_as_datetime(date: str | datetime.datetime | datetime.date, end_of_day=False) -> datetime.datetime:
    if isinstance(date, datetime.datetime):
        pass
    elif isinstance(date, str):
        date = dt_parse(date)
    elif isinstance(date, datetime.date):
        date = datetime.datetime.combine(date, datetime.datetime.min.time())
    elif not isinstance(date, datetime.datetime):
        raise ValueError(f"Invalid date type: {type(date)}")
    if end_of_day:
        date = date.replace(hour=23, minute=59, second=59)
    else:
        date = date.replace(hour=0, minute=0, second=0)
    return date


def datetime_to_solr(date: datetime.datetime) -> str:
    return date.strftime("%Y-%m-%dT%H:%M:%SZ")


class USApplicationManager(Manager["USApplication"]):
    default_filter = "appl_id"

    async def alen(self):
        api = PatentExaminationDataSystemApi()
        max_length = (await api.create_query(**self.get_query_params())).num_found
        return min(max_length, self.config.limit) if self.config.limit else max_length

    async def _aget_results(self) -> AsyncIterator["USApplication"]:
        query_params = self.get_query_params()
        api = PatentExaminationDataSystemApi()
        for start, rows in get_start_and_row_count(self.config.limit, self.config.offset, page_size=20):
            page = await api.create_query(**{**query_params, "start": start, "rows": rows})
            for app in page.applications:
                yield app
            if len(page.applications) < rows:
                break

    def get_query_params(self):
        # Short circuit processing logic if the "query" filter is specified
        if "query" in self.config.filter:
            query_text = self.config.filter["query"]
        else:
            query = list()
            date_filters = {
                QueryFields.get(k): v for k, v in self.config.filter.items() if QueryFields.is_date_field(k)
            }
            non_date_filters = {
                QueryFields.get(k): v for k, v in self.config.filter.items() if QueryFields.get(k) not in date_filters
            }

            date_filter_tuples = list()
            # Check date filters for validity
            for k, v in date_filters.items():
                if "__" in k:
                    f, *op = k.split("__")
                    if len(op) > 1:
                        raise ValueError(f"Invalid date filter: {k}={v}; Cannot have more than one operator")
                    if op[0] not in ("gte", "lte"):
                        raise ValueError(f"Invalid date filter: {k}={v}; Invalid operator: {op[0]}")
                    if isinstance(v, (list, tuple)):
                        raise ValueError(
                            f"Invalid date filter: {k}={v}; Cannot have multiple values with operator {op[0]}"
                        )
                    date_filter_tuples.append((f, op[0], cast_as_datetime(v)))
                else:
                    if isinstance(v, (list, tuple)):
                        if len(v) != 2:
                            raise ValueError(
                                f"Invalid date range filter: {k}; When filtering using a list or tuple, must have length 2 (start, end)"
                            )
                        date_filter_tuples.append((k, "gte", cast_as_datetime(v[0])))
                        date_filter_tuples.append((k, "lte", cast_as_datetime(v[1])))
                    else:
                        date_filter_tuples.append((k, "exact", cast_as_datetime(v)))
            # Create pairs of gte/lte filters
            query_date_filter_tuples = set()
            for k, op, v in date_filter_tuples:
                if op == "gte":
                    lte_query = next((v for k, op, v in date_filter_tuples if k == k and op == "lte"), "*")
                    query_date_filter_tuples.add((k, (v, lte_query)))
                elif op == "lte":
                    gte_query = next((v for k, op, v in date_filter_tuples if k == k and op == "gte"), "*")
                    query_date_filter_tuples.add((k, (gte_query, v)))
                elif op == "exact":
                    query_date_filter_tuples.add((k, (v, v)))

            # Create the query string
            for k, v in query_date_filter_tuples:
                start, end = v
                start = datetime_to_solr(cast_as_datetime(start))
                end = datetime_to_solr(cast_as_datetime(end, end_of_day=True))
                query.append(f"{k}:[{start} TO {end}]")

            # Add non-date filters
            for k, v in non_date_filters.items():
                if isinstance(v, Sequence) and not isinstance(v, str):
                    values = " OR ".join(str(i) for i in v)
                    query.append(f"{k}:({values})")
                else:
                    query.append(f"{k}:({v})")
            query_text = " AND ".join(query)

        if self.config.order_by:
            sort_query = ""
            for s in self.config.order_by:
                if s[0] == "-":
                    sort_query += f"{QueryFields.get(s)} desc ".strip()
                else:
                    sort_query += (f"{QueryFields.get(s)} asc").strip()
        else:
            sort_query = None

        mm = "0%" if "appEarlyPubNumber" not in query else "90%"

        query_data = {
            "query": query_text,
            "facet": False,
            "minimum_match": mm,
        }
        if sort_query:
            query_data["sort"] = sort_query
        return query_data

    @property
    def allowed_filters(self):
        return QueryFields.field_names()

    async def ais_online(self):
        return await PatentExaminationDataSystemApi.is_online()

    def is_online(self):
        return run_sync(self.ais_online())


class DocumentManager(Manager["Document"]):
    default_filter = "appl_id"

    async def alen(self):
        return len([i async for i in self])

    async def _aget_results(self) -> AsyncIterator["Document"]:
        api = PatentExaminationDataSystemApi()
        docs = await api.get_documents(self.config.filter["appl_id"])
        for doc in docs:
            yield doc

    def download(self, docs, path="."):
        run_sync(self.adownload(docs, path))

    async def adownload(self, docs, path="."):
        if str(path)[-4:].lower() == ".pdf":
            # If we've been given a specific filename, use it
            out_file = Path(path)
        else:
            out_file = Path(path) / "package.pdf"

        files = list()
        try:
            with TemporaryDirectory() as tmpdir:
                for doc in docs:
                    if doc.access_level_category == "PUBLIC":
                        files.append((await doc.adownload(tmpdir), doc))

                out_pdf = PdfMerger()
                page = 0
                for f, doc in files:
                    bookmark = f"{doc.mail_room_date} - {doc.code} - {doc.description}"
                    out_pdf.append(str(f), bookmark=bookmark, import_bookmarks=False)
                    page += doc.page_count

                out_pdf.write(str(out_file))
        except (PermissionError, NotADirectoryError):
            # This is needed due to a bug in Windows that prevents cleanup of the tmpdir
            pass
        return out_file
