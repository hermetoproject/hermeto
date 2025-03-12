from hermeto.core.config import get_config
from hermeto.core.models.input import Request
from hermeto.core.models.output import RequestOutput
from hermeto.core.package_managers.utils import merge_outputs
from hermeto.core.package_managers.yarn.main import fetch_yarn_source as fetch_yarnberry_source
from hermeto.core.package_managers.yarn_classic.main import MissingLockfile, NotV1Lockfile
from hermeto.core.package_managers.yarn_classic.main import (
    fetch_yarn_source as fetch_yarn_classic_source,
)


def fetch_yarn_source(request: Request) -> RequestOutput:
    """Fetch yarn source."""
    # Packages could be a mixture of yarn v1 and v2 (at least this is how it
    # looks now). To preserve this behavior each request is split into individual
    # packages which are assessed one by one.
    fetched_packages = []
    for package in request.yarn_packages:
        new_request = request.model_copy(update={"packages": [package]})
        try:
            fetched_packages.append(fetch_yarn_classic_source(new_request))
        except (MissingLockfile, NotV1Lockfile) as e:
            # It is assumed that if a package is not v1 then it is probably v2.
            if get_config().allow_yarnberry_processing:
                fetched_packages.append(fetch_yarnberry_source(new_request))
            else:
                raise e
    return merge_outputs(fetched_packages)
