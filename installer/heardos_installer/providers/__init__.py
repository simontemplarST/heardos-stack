from .base import Provider, Region, Plan, VpsSpec, VpsHandle, ProviderError, Account  # noqa: F401
from .cloudzy import Cloudzy  # noqa: F401
from .linode import Linode  # noqa: F401
from .vultr import Vultr  # noqa: F401
from .ovh import Ovh  # noqa: F401
from .byo import BringYourOwn  # noqa: F401
from .select import smart_default  # noqa: F401

PROVIDERS = {"cloudzy": Cloudzy, "linode": Linode, "vultr": Vultr, "ovh": Ovh, "byo": BringYourOwn}


def make(name: str, token: str = "", **kw):
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise ProviderError(f"unknown provider {name!r}; choose one of {sorted(PROVIDERS)}")
    return cls(token, **kw) if name != "byo" else cls(**kw)
