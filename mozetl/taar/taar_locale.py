"""
Bug 1396549 - TAAR Top addons per locale dictionary
This notebook is adapted from a gist that computes the top N addons per
locale after filtering for good candidates (e.g. no unsigned, no disabled,
...) [1].

[1] https://gist.github.com/mlopatka/46dddac9d063589275f06b0443fcc69d

"""

import click
import json
import logging

from pyspark.sql import SparkSession, Row
from pyspark.sql.functions import col, rank, desc
from pyspark.sql.window import Window
from .taar_utils import store_json_to_s3
from .taar_utils import load_amo_curated_whitelist

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LOCALE_FILE_NAME = "top10_dict"


def get_addons(spark):
    """
    Only Firefox release clients are considered.
    Columns are exploded (over addon keys)  to include locale of each addon
    installation instance system addons, disabled addons, unsigned addons
    are filtered out.
    Sorting by addon-installations and grouped by locale.

    Note that the final result of this job does not include firefox
    telemetry client ID so we do not need to post-process the data in the
    get_addons function.
    """
    return spark.sql(
        """
       WITH sample AS
       (
          SELECT
            client_id,
            locale AS locality,
            EXPLODE(active_addons)
          FROM
            clients_daily
          WHERE
            channel='release' AND
            app_name='Firefox'
        ),
        filtered_sample AS (
          SELECT
            locality,
            col.addon_id as addon_key
          FROM
            sample
          WHERE
            col.blocklisted = FALSE -- not blocklisted
            AND col.type = 'extension' -- nice webextensions only
            AND col.signed_state = 2 -- fully reviewed addons only
            AND col.user_disabled = FALSE -- active addons only get counted
            AND col.app_disabled = FALSE -- exclude compatibility disabled
            AND col.is_system = FALSE -- exclude system addons
            AND locality <> 'null'
            AND col.addon_id is not null
        ),
        country_addon_pairs AS (
        SELECT
        COUNT(*) AS pair_cnts, addon_key, locality
        from filtered_sample
        GROUP BY locality, addon_key
        )

        SELECT
            pair_cnts,
            addon_key,
            locality
        FROM
            country_addon_pairs
        ORDER BY locality, pair_cnts DESC
    """
    )


def compute_threshold(addon_df):
    """ Get a threshold to remove locales with a small
    number of addons installations.
    """
    addon_install_counts = addon_df.groupBy("locality").agg({"pair_cnts": "sum"})

    # Compute a threshold at the 25th percentile to remove locales with a
    # small number of addons installations.
    locale_pop_threshold = addon_install_counts.approxQuantile(
        "sum(pair_cnts)", [0.25], 0.2
    )[0]

    # Safety net in case the distribution gets really skewed, we should
    # require 2000 addon installation instances to make recommendations.
    if locale_pop_threshold < 2000:
        locale_pop_threshold = 2000

    # Filter the list to only include locales including a sufficient
    # number of addon installations. Include number of addons in locales
    # that satisfy the threshold condition.
    addon_locale_counts = addon_install_counts.filter(
        (col("sum(pair_cnts)") >= locale_pop_threshold)
    )

    return addon_locale_counts


def transform(addon_df, addon_locale_counts_df, num_addons):
    """ Converts the locale-specific addon data in to a dictionary.

    :param addon_df: the locale-specific addon dataframe;
    :param addon_locale_counts_df: total addon-installs per locale;
    :param num_addons: requested number of recommendations.
     :return: a dictionary {<locale>: [['GUID1', 1.0], ['GUID2', 0.9], ...]}
    """

    # Helper function to normalize addon installations in a lambda.
    def normalize_cnts(p):
        loc_norm = float(p["pair_cnts"]) / float(p["sum(pair_cnts)"])
        return loc_norm

    # Instantiate an empty dict.
    top10_per = {}

    # Join addon pair counts with total addon installs per locale.
    # need to clone the DFs to workaround for SPARK bug#14948
    # https://issues.apache.org/jira/browse/SPARK-14948
    df1 = addon_locale_counts_df.alias("df1")
    df2 = addon_df.alias("df2")

    combined_df = addon_df.join(df1, df2.locality == df1.locality).drop(df1.locality)

    # Normalize installation rate per locale.
    normalized_installs = combined_df.rdd.map(
        lambda p: Row(
            addon_key=p["addon_key"], locality=p["locality"], loc_norm=normalize_cnts(p)
        )
    ).toDF()

    # Groupby locale and sort by normalized install rate
    window = Window.partitionBy(normalized_installs["locality"]).orderBy(
        desc("loc_norm")
    )

    # Truncate reults exceeding required number of addons.
    truncated_df = (
        normalized_installs.select("*", rank().over(window).alias("rank"))
        .filter(col("rank") <= num_addons)
        .drop(col("rank"))
    )

    list_of_locales = [
        x[0] for x in truncated_df.select(truncated_df.locality).distinct().collect()
    ]

    # There is probably a *much* smarter way fo doing this...
    # but alas, I am le tired.
    for specific_locale in list_of_locales:
        # Most popular addons per locale sorted by normalized
        # number of installs.
        top10_per[specific_locale] = [
            [x["addon_key"], x["loc_norm"]]
            for x in truncated_df.filter(
                truncated_df.locality == specific_locale
            ).collect()
        ]
    return top10_per


def generate_dictionary(spark, num_addons):
    """ Wrap the dictionary generation functions in an
    easily testable way.
    """
    # Execute spark.SQL query to get fresh addons from clients_daily.
    addon_df = get_addons(spark)

    # Load external whitelist based on AMO data.
    amo_whitelist = load_amo_curated_whitelist()

    # Filter to include only addons present in AMO whitelist.
    addon_df_filtered = addon_df.where(col("addon_key").isin(amo_whitelist))

    # Make sure not to include addons from very small locales.
    addon_locale_counts_df = compute_threshold(addon_df_filtered)
    return transform(addon_df_filtered, addon_locale_counts_df, num_addons)


@click.command()
@click.option("--date", required=True)
@click.option("--bucket", default="telemetry-private-analysis-2")
@click.option("--prefix", default="taar/locale/")
@click.option("--num_addons", default=10)
def main(date, bucket, prefix, num_addons):
    spark = (
        SparkSession.builder.appName("taar_locale").enableHiveSupport().getOrCreate()
    )

    logger.info("Processing top N addons per locale")
    locale_dict = generate_dictionary(spark, num_addons)
    store_json_to_s3(
        json.dumps(locale_dict, indent=2), LOCALE_FILE_NAME, date, prefix, bucket
    )

    spark.stop()
