import ujson as json
import boto3

from datetime import datetime, timedelta
from moztelemetry.dataset import Dataset
from .utils import get_short_and_long_spinners


def run_spinner_etl(sc):
    nightly_build_channels = ["nightly", "aurora"]
    sample_size = 1.0

    probe_available = datetime(2016, 9, 8)
    look_back_date = datetime.today() - timedelta(days=180)
    start_date = max(probe_available, look_back_date).strftime("%Y%m%d")
    end_date = datetime.today().strftime("%Y%m%d")

    def appBuildId_filter(b):
        return (
            (b.startswith(start_date) or b > start_date) and
            (b.startswith(end_date) or b < end_date)
        )

    print "Start Date: {}, End Date: {}".format(start_date, end_date)

    build_results = {}

    for build_type in nightly_build_channels:
        # Bug 1341340 - if we're looking for pings from before 20161012, we need to query
        # old infra.
        old_infra_pings = Dataset.from_source("telemetry-oldinfra") \
            .where(docType='main') \
            .where(submissionDate=lambda b: b < "20161201") \
            .where(appBuildId=appBuildId_filter) \
            .where(appUpdateChannel=build_type) \
            .records(sc, sample=sample_size)

        new_infra_pings = Dataset.from_source("telemetry") \
            .where(docType='main') \
            .where(submissionDate=lambda b: (b.startswith("20161201") or b > "20161201")) \
            .where(appBuildId=appBuildId_filter) \
            .where(appUpdateChannel=build_type) \
            .records(sc, sample=sample_size)

        pings = old_infra_pings.union(new_infra_pings)
        build_results[build_type] = get_short_and_long_spinners(pings)

    s3_client = boto3.client('s3')
    for result_key, results in build_results.iteritems():
        filename = "severities_by_build_id_%s.json" % result_key
        results_json = json.dumps(results, ensure_ascii=False)

        with open(filename, 'w') as f:
            f.write(results_json)

        s3_client.upload_file(
            filename,
            'telemetry-public-analysis-2',
            'spinner-severity-generator/data/{}'.format(filename)
        )
