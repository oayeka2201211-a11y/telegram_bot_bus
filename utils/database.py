# utils/database.py — Firebase Firestore adapter for ZISTACK
import os
import json
import re
import base64
from datetime import datetime
from typing import Any, Dict
from dotenv import load_dotenv
from utils.logger import logger

load_dotenv()

# Firebase admin SDK
import firebase_admin
from firebase_admin import credentials, firestore

# Credential resolution order:
# 1) FIREBASE_SERVICE_ACCOUNT env var (JSON or base64-encoded JSON)
# 2) GOOGLE_APPLICATION_CREDENTIALS env var (path to file)
# 3) service account JSON file in repo root (candidate)

FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# candidate file in repo root
candidate = os.path.join(os.path.dirname(os.path.dirname(__file__)), "zistack-76128-firebase-adminsdk-fbsvc-641b689f33.json")
if not SERVICE_ACCOUNT_FILE and os.path.exists(candidate):
    SERVICE_ACCOUNT_FILE = candidate

_app = None
_firestore_client = None
try:
    if not firebase_admin._apps:
        cred = None

        # 1) Try env var JSON or base64
        if FIREBASE_SERVICE_ACCOUNT:
            try:
                try:
                    sa_dict = json.loads(FIREBASE_SERVICE_ACCOUNT)
                except Exception:
                    # try base64 decode
                    sa_json = base64.b64decode(FIREBASE_SERVICE_ACCOUNT).decode('utf-8')
                    sa_dict = json.loads(sa_json)
                cred = credentials.Certificate(sa_dict)
                logger.info("Loaded Firebase credentials from FIREBASE_SERVICE_ACCOUNT env var")
            except Exception as e:
                logger.error(f"FIREBASE_SERVICE_ACCOUNT provided but failed to parse: {e}")
                cred = None

        # 2) Try file path in GOOGLE_APPLICATION_CREDENTIALS or candidate
        if cred is None and SERVICE_ACCOUNT_FILE:
            if os.path.exists(SERVICE_ACCOUNT_FILE):
                try:
                    cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)
                    logger.info(f"Loaded Firebase credentials from file: {SERVICE_ACCOUNT_FILE}")
                except Exception as e:
                    logger.error(f"Failed to load credentials from {SERVICE_ACCOUNT_FILE}: {e}")
            else:
                logger.warning(f"GOOGLE_APPLICATION_CREDENTIALS is set but file not found: {SERVICE_ACCOUNT_FILE}")

        # Initialize app if we have credentials
        if cred is not None:
            _app = firebase_admin.initialize_app(cred)
            logger.info("Initialized Firebase app with Firestore")
        else:
            # Last resort: try default credentials — will raise useful error in environments without creds
            try:
                _app = firebase_admin.initialize_app()
                logger.info("Initialized Firebase app with default credentials")
            except Exception as e:
                # fail loudly with a helpful message
                msg = (
                    "No valid Firebase credentials found. Provide either:\n"
                    "  - FIREBASE_SERVICE_ACCOUNT env var (JSON text or base64), or\n"
                    "  - GOOGLE_APPLICATION_CREDENTIALS env var pointing to the service account file on the container, or\n"
                    f"  - place the service account JSON at: {candidate}\n"
                    f"Original error: {e}"
                )
                logger.error(msg)
                raise RuntimeError(msg)
    else:
        _app = firebase_admin.get_app()
    _firestore_client = firestore.client()
except Exception as e:
    logger.error(f"Failed to initialize Firebase app / Firestore client: {e}")
    raise


# --- Lightweight collection wrapper to emulate minimal pymongo API ---

def _serialize_value(value: Any) -> Any:
    """Recursively serialize values that are not JSON-serializable by Firestore writes."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


class FirebaseCollection:
    def __init__(self, path: str):
        self.path = path
        self.collection = _firestore_client.collection(path)

    def _snapshot_to_doc(self, snapshot):
        if not snapshot.exists:
            return None
        doc = snapshot.to_dict() or {}
        doc.setdefault('_id', snapshot.id)
        doc.setdefault('id', snapshot.id)
        return doc

    def _all_items(self) -> Dict[str, dict]:
        items = {}
        for snapshot in self.collection.stream():
            doc = self._snapshot_to_doc(snapshot)
            if doc is not None:
                items[snapshot.id] = doc
        return items

    def insert_one(self, data: Dict[str, Any]):
        doc_ref = self.collection.document()
        key = doc_ref.id
        data = dict(data)
        data.setdefault('created_at', datetime.utcnow().isoformat())
        data['_id'] = key
        data['id'] = key

        data = _serialize_value(data)

        doc_ref.set(data)
        class Res: pass
        res = Res()
        res.inserted_id = key
        return res

    def find_one(self, query: Dict[str, Any]):
        if '_id' in query and len(query) == 1:
            return self.get_by_key(str(query['_id']))
        items = self._all_items()
        for key, doc in items.items():
            if self._match(doc, key, query):
                return doc
        return None

    def get_by_key(self, key: str):
        snapshot = self.collection.document(str(key)).get()
        return self._snapshot_to_doc(snapshot)

    def find(self, query: Dict[str, Any]):
        items = self._all_items()
        res = []
        for key, doc in items.items():
            if self._match(doc, key, query):
                doc_copy = dict(doc)
                doc_copy.setdefault('_id', key)
                doc_copy.setdefault('id', key)
                res.append(doc_copy)
        return res

    def delete_one(self, query: Dict[str, Any]):
        if '_id' in query:
            self.collection.document(str(query['_id'])).delete()
            class R: pass
            r = R()
            r.deleted_count = 1
            return r
        items = self._all_items()
        for k, doc in items.items():
            if self._match(doc, k, query):
                self.collection.document(str(k)).delete()
                class R: pass
                r = R()
                r.deleted_count = 1
                return r
        class R: pass
        r = R()
        r.deleted_count = 0
        return r

    def delete_by_key(self, key: str):
        self.collection.document(str(key)).delete()
        class R: pass
        r = R()
        r.deleted_count = 1
        return r

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        items = self._all_items()
        matched = 0
        modified = 0
        for k, doc in items.items():
            if self._match(doc, k, query):
                matched += 1
                doc = dict(doc)

                # support $set updates
                if isinstance(update, dict) and '$set' in update:
                    for field, val in update['$set'].items():
                        doc[field] = _serialize_value(val)

                # support $push updates (append to list fields)
                if isinstance(update, dict) and '$push' in update:
                    for field, val in update['$push'].items():
                        lst = doc.get(field) or []
                        # append the raw value (serialized as needed)
                        lst.append(_serialize_value(val))
                        doc[field] = lst

                # support plain dict updates (set fields directly)
                if isinstance(update, dict) and '$set' not in update and '$push' not in update:
                    for field, val in update.items():
                        doc[field] = _serialize_value(val)

                self.collection.document(str(k)).set(doc)
                modified += 1
                break
        if matched == 0 and upsert:
            # insert
            merged = dict(query)
            if isinstance(update, dict) and '$set' in update:
                merged.update(update['$set'])
            if isinstance(update, dict) and '$push' in update:
                # for upsert, initialize pushed lists with the pushed values
                for field, val in update['$push'].items():
                    merged[field] = [ _serialize_value(val) ]
            self.insert_one(merged)
            matched = 1
            modified = 1
        class R: pass
        r = R()
        r.matched_count = matched
        r.modified_count = modified
        return r

    def set_by_key(self, key: str, data: Dict[str, Any]):
        data = dict(data)
        data = _serialize_value(data)
        self.collection.document(str(key)).set(data)

    def transaction(self, key: str, update_fn):
        doc_ref = self.collection.document(str(key))
        transaction = _firestore_client.transaction()

        @firestore.transactional
        def _run_in_transaction(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            current = self._snapshot_to_doc(snapshot) if snapshot.exists else None
            updated = update_fn(current)

            if updated is None:
                transaction.delete(doc_ref)
                return None

            payload = _serialize_value(dict(updated))
            payload.setdefault('_id', str(key))
            payload.setdefault('id', str(key))
            transaction.set(doc_ref, payload)
            return payload

        return _run_in_transaction(transaction)

    def create_index(self, *args, **kwargs):
        # no-op for Firebase
        return None

    def __repr__(self):
        return f"FirebaseCollection({self.path})"


# --- Collections similar to previous interface ---
sellers_collection = FirebaseCollection('sellers')
buyers_collection = FirebaseCollection('buyers')
images_collection = FirebaseCollection('images')
products_collection = FirebaseCollection('products')
cart_collection = FirebaseCollection('cart')
payments_collection = FirebaseCollection('payments')
orders_collection = FirebaseCollection('orders')

# expose old names for compatibility
products = products_collection
payments = payments_collection
orders = orders_collection

# --- Helper functions matching previous API ---

def is_connected() -> bool:
    try:
        next(sellers_collection.collection.limit(1).stream(), None)
        return True
    except Exception:
        return False


def get_seller_by_business_name(business_name: str):
    try:
        logger.info(f"Querying seller by business_name='{business_name}' in sellers")
        return sellers_collection.find_one({"business_name": business_name})
    except Exception as e:
        logger.error(f"Error in get_seller_by_business_name: {e}")
        return None


def update_seller(business_name: str, data: dict):
    try:
        res = sellers_collection.update_one({"business_name": business_name}, {"$set": data}, upsert=False)
        logger.info(f"update_seller matched={res.matched_count} modified={res.modified_count}")
        return res.matched_count > 0
    except Exception as e:
        logger.error(f"Error in update_seller: {e}")
        return False


def insert_new_seller(data: dict):
    try:
        data = dict(data)
        data.setdefault("role", "seller")
        if "business_name" not in data:
            logger.error("insert_new_seller called without business_name")
            return None
        data.setdefault("created_at", datetime.utcnow().isoformat())
        data.setdefault("last_active", datetime.utcnow().isoformat())
        data.setdefault("verified", False)
        res = sellers_collection.insert_one(data)
        logger.info(f"Inserted new seller id={res.inserted_id} into sellers")
        return res
    except Exception as e:
        logger.error(f"Error in insert_new_seller: {e}")
        return None


def get_user_by_telegram_id(telegram_id: int):
    try:
        return sellers_collection.find_one({"telegram_id": telegram_id})
    except Exception as e:
        logger.error(f"Error in get_user_by_telegram_id: {e}")
        return None


def update_user_activity(telegram_id: int):
    try:
        sellers_collection.update_one({"telegram_id": telegram_id}, {"$set": {"last_active": datetime.utcnow().isoformat()}})
    except Exception as e:
        logger.error(f"Error in update_user_activity: {e}")


def get_all_sellers():
    try:
        return sellers_collection.find({})
    except Exception as e:
        logger.error(f"Error in get_all_sellers: {e}")
        return []


def delete_seller(business_name: str):
    try:
        r = sellers_collection.delete_one({"business_name": business_name})
        return r.deleted_count > 0
    except Exception as e:
        logger.error(f"Error in delete_seller: {e}")
        return False


def insert_new_buyer(data: dict):
    try:
        data = dict(data)
        data.setdefault("created_at", datetime.utcnow().isoformat())
        data.setdefault("last_active", datetime.utcnow().isoformat())
        data.setdefault("verified", False)
        res = buyers_collection.insert_one(data)
        logger.info(f"Inserted new buyer id={res.inserted_id} into buyers")
        return res
    except Exception as e:
        logger.error(f"Error in insert_new_buyer: {e}")
        return None


# --- Utility: simple matcher for queries ---
def _match_value(value, query_val):
    # handle regex dict
    if isinstance(query_val, dict) and '$regex' in query_val:
        pattern = query_val['$regex']
        options = query_val.get('$options', '')
        flags = re.IGNORECASE if 'i' in options else 0
        if isinstance(value, (list, tuple, set)):
            return any(re.search(pattern, str(item), flags) is not None for item in value)
        return re.search(pattern, str(value), flags) is not None

    if isinstance(value, (list, tuple, set)):
        return query_val in value

    # direct equality
    return value == query_val


def _match_doc(doc: dict, key: str, query: Dict[str, Any]) -> bool:
    for field, q in query.items():
        if field == '_id':
            if key != str(q):
                return False
            continue
        val = doc.get(field)
        if val is None and isinstance(q, dict) and '$regex' in q:
            # allow regex on absent field
            return False
        if not _match_value(val, q):
            return False
    return True

# wire matcher into class for compatibility
FirebaseCollection._match = staticmethod(_match_doc)

 
