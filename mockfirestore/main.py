import operator
import random
import string
from datetime import datetime as dt
from collections import OrderedDict
from functools import reduce
from itertools import islice
from typing import Dict, Any, List, Tuple, TypeVar, Sequence, Callable, Optional, Iterator

T = TypeVar('T')
KeyValuePair = Tuple[str, Dict[str, Any]]
Document = Dict[str, Any]
Collection = Dict[str, Document]
Store = Dict[str, Collection]

# by analogy with
# https://github.com/mongomock/mongomock/blob/develop/mongomock/__init__.py
# try to import gcloud exceptions
# and if gcloud is not installed, define our own
try:
    from google.cloud.exceptions import ClientError, Conflict, AlreadyExists
except ImportError:
    class ClientError(Exception):
        code = None

        def __init__(self, message, *args):
            self.message = message
            super().__init__(message, *args)

        def __str__(self):
            return "{} {}".format(self.code, self.message)

    class Conflict(ClientError):
        code = 409

    class AlreadyExists(Conflict):
        pass


class Timestamp:
    """
    Imitates some properties of `google.protobuf.timestamp_pb2.Timestamp`
    """

    def __init__(self, timestamp: float):
        self._timestamp = timestamp

    @classmethod
    def from_now(cls):
        timestamp = dt.now().timestamp()
        return cls(timestamp)

    @property
    def seconds(self):
        return str(self._timestamp).split('.')[0]

    @property
    def nanos(self):
        return str(self._timestamp).split('.')[1]


class DocumentSnapshot:
    def __init__(self, doc: Document) -> None:
        self._doc = doc

    @property
    def exists(self) -> bool:
        return self._doc != {}

    def to_dict(self) -> Document:
        return self._doc

    @property
    def create_time(self) -> Timestamp:
        timestamp = Timestamp.from_now()
        return timestamp


class DocumentReference:
    def __init__(self, data: Store, path: List[str]) -> None:
        self._data = data
        self._path = path

    @property
    def id(self):
        return self._path[-1]

    def get(self) -> DocumentSnapshot:
        return DocumentSnapshot(get_by_path(self._data, self._path))

    def delete(self):
        delete_by_path(self._data, self._path)

    def set(self, data: Dict, merge=False):
        if merge:
            self.update(data)
        else:
            set_by_path(self._data, self._path, data)

    def update(self, data: Dict[str, Any]):
        get_by_path(self._data, self._path).update(data)

    def collection(self, name) -> 'CollectionReference':
        document = get_by_path(self._data, self._path)
        new_path = self._path + [name]
        if name not in document:
            set_by_path(self._data, new_path, {})
        return CollectionReference(self._data, new_path)


class Query:
    def __init__(self, data: Collection) -> None:
        if isinstance(data, OrderedDict):
            self._data = data
        else:
            self._data = OrderedDict(sorted(data.items(), key=lambda t: t[0]))

    def get(self) -> Iterator[DocumentSnapshot]:
        return (DocumentSnapshot(doc) for doc in self._data.values())

    def where(self, field: str, op: str, value: Any) -> 'Query':
        compare = self._compare_func(op)
        filtered = OrderedDict((k, v) for k, v in self._data.items() if compare(v[field], value))
        return Query(filtered)

    def order_by(self, key: str, direction: Optional[str] = 'ASCENDING') -> 'Query':
        sorted_items = sorted(self._data.items(), key=lambda doc: doc[1][key], reverse=direction == 'DESCENDING')
        return Query(OrderedDict(sorted_items))

    def limit(self, limit_amount: int) -> 'Query':
        limited = islice(self._data.items(), limit_amount)
        return Query(OrderedDict(limited))

    def _compare_func(self, op: str) -> Callable[[T, T], bool]:
        if op == '==':
            return lambda x, y: x == y
        elif op == '<':
            return lambda x, y: x < y
        elif op == '<=':
            return lambda x, y: x <= y
        elif op == '>':
            return lambda x, y: x > y
        elif op == '>=':
            return lambda x, y: x >= y


class CollectionReference:
    def __init__(self, data: Store, path: List[str]) -> None:
        self._data = data
        self._path = path

    def document(self, name: Optional[str] = None) -> DocumentReference:
        collection = get_by_path(self._data, self._path)
        if name is None:
            name = generate_random_string()
        new_path = self._path + [name]
        if name not in collection:
            set_by_path(self._data, new_path, {})
        return DocumentReference(self._data, new_path)

    def add(self, document_data: Dict, document_id: str = None) \
            -> Tuple[Timestamp, DocumentReference]:
        if document_id is None:
            document_id = document_data.get('id', generate_random_string())
        collection = get_by_path(self._data, self._path)
        new_path = self._path + [document_id]
        if document_id in collection:
            raise AlreadyExists('Document already exists: {}'.format(new_path))
        doc_ref = DocumentReference(self._data, new_path)
        doc_ref.set(document_data)
        timestamp = Timestamp.from_now()
        return timestamp, doc_ref

    def get(self) -> Iterator[DocumentSnapshot]:
        collection = get_by_path(self._data, self._path)
        return Query(collection).get()

    def where(self, field: str, op: str, value: Any) -> Query:
        collection = get_by_path(self._data, self._path)
        return Query(collection).where(field, op, value)

    def order_by(self, key: str, direction: Optional[str] = None) -> Query:
        collection = get_by_path(self._data, self._path)
        return Query(collection).order_by(key, direction)

    def limit(self, limit_amount: int) -> Query:
        collection = get_by_path(self._data, self._path)
        return Query(collection).limit(limit_amount)

    def list_documents(self, page_size: Optional[int] = None) -> Sequence[DocumentReference]:
        docs = []
        for key in get_by_path(self._data, self._path):
            docs.append(self.document(key))
        return docs


class MockFirestore:

    def __init__(self) -> None:
        self._data = {}

    def collection(self, name: str) -> CollectionReference:
        if name not in self._data:
            self._data[name] = {}
        return CollectionReference(self._data, [name])

    def reset(self):
        self._data = {}


def get_by_path(data: Dict[str, T], path: Sequence[str]) -> T:
    """Access a nested object in root by item sequence."""
    return reduce(operator.getitem, path, data)


def set_by_path(data: Dict[str, T], path: Sequence[str], value: T):
    """Set a value in a nested object in root by item sequence."""
    get_by_path(data, path[:-1])[path[-1]] = value


def delete_by_path(data: Dict[str, T], path: Sequence[str]):
    """Delete a value in a nested object in root by item sequence."""
    del get_by_path(data, path[:-1])[path[-1]]


def generate_random_string():
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(20))
