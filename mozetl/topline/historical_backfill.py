"""
# Topline Historical Backfill

This script provides a way to backfill a summmary period from
historical data generated by the executive_reporting pipeline
[1]. This data is stored in two files representing the monthly and
weekly topline summaries. The reporting pipeline has been rewritten in
`telemetry-batch-view` as the ToplineSummaryView, which writes out
data to `telemetry-parquet/topline_summary/v1`.

Example Usage:

```
python -m mozetl.topline.historical_backfill \
    s3://path/to/v4_weekly.csv \
    weekly \
    net-mozaws-prod-us-west-2-pipeline-analysis \
    --prefix test-topline/topline_summary/v1
```

[1] https://git.io/v9mxF
"""
import logging

import click

from .schema import historical_schema, topline_schema
from pyspark.sql import SparkSession, functions as F


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def format_output_path(bucket, prefix):
    return "s3://{}/{}".format(bucket, prefix)


def backfill_topline_summary(historical_df, path, batch=False, overwrite=False):
    """ Backfill the topline summary with the historical dataframe.

    :historical_df dataframe: Data from the v4 historical data csv
    :path str: spark compatible path string to save partitioned parquet data
    """

    df = (
        historical_df
        .where(
              (F.col('geo') != 'all') &
              (F.col('os') != 'all') &
              (F.col('channel') != 'all'))
        .withColumn('report_start', F.date_format('date', 'yyyyMMdd'))
    )

    # Cast all elements from the csv file. Assume both schemas are flat.
    df = df.select(*[F.col(f.name).cast(f.dataType).alias(f.name)
                     for f in topline_schema.fields])

    logging.info("Saving historical data to {}.".format(path))

    write_mode = "overwrite" if overwrite else "error"
    writer = df.write.mode(write_mode)

    # Use the same parititoning scheme as topline_summary
    if batch:
        writer = writer.partitionBy('report_start')
    else:
        # The csv file should be read from
        # telemetry-private-analysis=2/executive-report. This folder contains csv
        # files that should contain only a single date. Assert that property here.
        path = "{}/report_start={}".format(path, df.head().report_start)
        if df.select('report_start').distinct().count() != 1:
            raise RuntimeError("There should only be a single reporting date")

    writer.parquet(path)


@click.command()
@click.argument('source_s3_path')
@click.argument('mode', type=click.Choice(['weekly', 'monthly']))
@click.argument('bucket')
@click.option('--prefix', default='topline_summary/v1')
@click.option('--batch/--no-batch', default=False,
        help='Used when the csv file contains multiple reporting periods')
@click.option('--overwrite/--no-overwrite', default=False,
        help='Overwrite existing data. Caution when applying this batch mode,'
             'since it will overwrite all data in the repository.')
def main(source_s3_path, mode, bucket, prefix, batch, overwrite):

    if batch and overwrite:
        click.confirm('CAUTION: Using --batch and --overwrite will completely '
                'overwrite the prefix with input data. Do you want to '
                'continue?', abort=True)

    spark = (SparkSession
             .builder
             .appName('topline_historical_backfill')
             .getOrCreate())

    logging.info("Running historical backfill for {} executive report at {}."
                 .format(mode, source_s3_path))

    historical_df = spark.read.csv(source_s3_path,
                                   schema=historical_schema,
                                   header=True)
    output_path = format_output_path(bucket, '{}/mode={}'.format(prefix, mode))
    try:
        backfill_topline_summary(historical_df, output_path, batch, overwrite)
    finally:
        spark.stop()
    logging.info("Finished historical backfill job.")


if __name__ == '__main__':
    main()
