"""Public queue calls and a data-only wire format. The guest receives no test outcomes."""

import json
import math
import sys
from datetime import datetime


def pack(value, delivery=None):
    kind = type(value)
    if value is None or kind in (str, bool, int) or (kind is float and math.isfinite(value)):
        return value
    if kind is datetime:
        return {"datetime": value.isoformat()}
    if kind is delivery:
        return {"delivery": [pack(value.event_id), pack(value.channel), pack(value.due_at)]}
    if kind in (tuple, list):
        items = [pack(item, delivery) for item in value]
        return {"tuple": items} if kind is tuple else items
    return {"opaque": kind.__name__}


def unpack(value, delivery=None):
    if value is None or type(value) in (str, bool, int, float):
        return value
    if type(value) is list:
        return [unpack(item, delivery) for item in value]
    if type(value) is not dict or len(value) != 1:
        raise ValueError("invalid public return value")
    tag, body = next(iter(value.items()))
    if tag == "datetime" and type(body) is str:
        return datetime.fromisoformat(body)
    if tag == "tuple" and type(body) is list:
        return tuple(unpack(item, delivery) for item in body)
    if tag == "delivery" and delivery is not None and type(body) is list and len(body) == 3:
        return delivery(*(unpack(item, delivery) for item in body))
    if tag == "opaque" and type(body) is str:
        return object()
    raise ValueError("unknown public return type")


def exception(error):
    return {"type": type(error).__name__, "value_error": isinstance(error, ValueError)}


def main():
    operations = json.load(sys.stdin)
    sys.path.insert(0, "/fixture")
    try:
        from notify.service import Delivery, NotificationQueue
    except Exception as error:
        reply = {"import_exception": exception(error)}
    else:
        groups = []
        for calls in operations:
            queue = None
            responses = []
            for name, args, kwargs in calls:
                args = [unpack(arg) for arg in args]
                kwargs = {key: unpack(arg) for key, arg in kwargs.items()}
                try:
                    if name == "new":
                        queue = NotificationQueue(*args, **kwargs)
                        result = None
                    else:
                        result = getattr(queue, name)(*args, **kwargs)
                    response = {"value": pack(result, Delivery)}
                except Exception as error:
                    response = {"exception": exception(error)}
                responses.append(response)
            groups.append(responses)
        reply = {"observations": groups}
    json.dump(reply, sys.stdout, separators=(",", ":"), allow_nan=False)


if __name__ == "__main__":
    main()
