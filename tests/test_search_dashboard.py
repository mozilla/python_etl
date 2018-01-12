import functools
import pytest
from collections import namedtuple
from pyspark.sql.types import (
    StructField, ArrayType, BooleanType, StringType, LongType, StructType, DoubleType
)
from mozetl.search.dashboard import (
    search_dashboard_etl, explode_search_counts, add_derived_columns
)


# Some boilerplate to help define example dataframes for testing

# A helper class for declaratively creating dataframe factories
dataframe_field = namedtuple('dataframe_field', [
    'name',
    'default_value',
    'type',
    'nullable',
])


def to_field(field_tuple):
    """Create a dataframe_field from a tuple"""
    return dataframe_field(*field_tuple)


def get_dataframe_factory_config(fields):
    """Parse a list of dataframe_fields to a schema and set of default values"""
    schema = StructType([
        StructField(field.name, field.type, field.nullable)
        for field in fields
    ])
    default_sample = {field.name: field.default_value for field in fields}

    return schema, default_sample


@pytest.fixture
def define_dataframe_factory(dataframe_factory):
    def partial(fields):
        """Create a dataframe_factory from a set of field configs"""
        schema, default_sample = get_dataframe_factory_config(fields)
        return functools.partial(
            dataframe_factory.create_dataframe,
            base=default_sample,
            schema=schema
        )

    return partial


# Boilerplate for generating example main_summary tables
def generate_search_count(engine='google', source='urlbar', count=4):
    return {
        'engine': engine,
        'source': source,
        'count':  count,
    }


def generate_addon(addon_id, name, version):
    return {
        'addon_id': addon_id,
        'name': name,
        'version': version,
    }


@pytest.fixture()
def generate_main_summary_data(define_dataframe_factory):
    search_type = ArrayType(StructType([
        StructField('engine', StringType(), False),
        StructField('source', StringType(), False),
        StructField('count',  LongType(),   False),
    ]))
    addons_type = ArrayType(StructType([
        StructField('addon_id', StringType(), False),
        StructField('blocklisted', BooleanType(), True),
        StructField('name', StringType(), True),
        StructField('user_disabled', BooleanType(), True),
        StructField('app_disabled', BooleanType(), True),
        StructField('version', StringType(), True),
        StructField('scope', LongType(), True),
        StructField('type', StringType(), True),
        StructField('foreign_install', BooleanType(), True),
        StructField('has_binary_components', BooleanType(), True),
        StructField('install_day', LongType(), True),
        StructField('update_day', LongType(), True),
        StructField('signed_state', LongType(), True),
        StructField('is_system', BooleanType(), True),
        StructField('is_web_extension', BooleanType(), True),
        StructField('multiprocess_compatible', BooleanType(), True),
    ]))

    return define_dataframe_factory(map(to_field, [
        ('submission_date', '20170101',                StringType(), False),
        ('country',         'DE',                      StringType(), True),
        ('locale',          'de',                      StringType(), True),
        ('search_cohort',   None,                      StringType(), True),
        ('app_version',     '54.0.1',                  StringType(), True),
        ('distribution_id', None,                      StringType(), True),
        ('ignored_col',     1.0,                       DoubleType(), True),
        ('search_counts',   [generate_search_count()], search_type,  True),
        ('active_addons',   [generate_addon('random@mozilla.com', 'random', '0.1'),
                             generate_addon('followonsearch@mozilla.com',
                                            'Follow-on Search Telemetry',
                                            '0.9.5')], addons_type, True),
    ]))


@pytest.fixture
def main_summary(generate_main_summary_data):
    return generate_main_summary_data(
        [
            {
                'country': 'US',
                'ignored_col': 3.14,
            },
            {'app_version': '52.0.3'},
            {'distribution_id': 'totally not null'},
            {'search_counts': [
                generate_search_count(engine='bing'),
                generate_search_count(engine='yahoo'),
            ]}
        ] +
        [{}] * 5  # Some duplicate default rows to test aggregation
    )


@pytest.fixture
def simple_main_summary(generate_main_summary_data):
    return generate_main_summary_data(
        [
            {'search_counts': [
                generate_search_count(engine='bing'),
                generate_search_count(engine='yahoo'),
            ]}
        ]
    )


@pytest.fixture()
def generate_exploded_data(define_dataframe_factory):
    return define_dataframe_factory(map(to_field, [
        ('submission_date', '20170101', StringType(), False),
        ('country',         'DE',       StringType(), True),
        ('locale',          'de',       StringType(), True),
        ('search_cohort',   None,       StringType(), True),
        ('app_version',     '54.0.1',   StringType(), True),
        ('distribution_id', None,       StringType(), True),
        ('addon_version',   '0.9.5',    StringType(), False),
        ('engine',          'google',   StringType(), False),
        ('source',          'urlbar',   StringType(), False),
        ('count',           4,          LongType(),   False),
    ]))


@pytest.fixture()
def exploded_simple_main_summary(generate_exploded_data):
    return generate_exploded_data([
        {'engine': 'yahoo'},
        {'engine': 'bing'},
    ])


@pytest.fixture()
def exploded_data_for_derived_cols(generate_exploded_data):
    return generate_exploded_data([
        {'source': 'sap:urlbar:SomeCodeHere'},
        {'source': 'follow-on:urlbar:SomeCodeHere'},
        {'source': 'urlbar'},
    ])


@pytest.fixture()
def derived_columns(define_dataframe_factory):
    # template for the expected results
    factory = define_dataframe_factory(map(to_field, [
        ('submission_date', '20170101',   StringType(), False),
        ('country',         'DE',         StringType(), True),
        ('locale',          'de',         StringType(), True),
        ('search_cohort',   None,         StringType(), True),
        ('app_version',     '54.0.1',     StringType(), True),
        ('distribution_id', None,         StringType(), True),
        ('addon_version',   '0.9.5',      StringType(), False),
        ('engine',          'google',     StringType(), False),
        ('source',          'urlbar',     StringType(), False),
        ('count',           4,            LongType(),   False),
        ('type',            'chrome-sap', StringType(), False),
    ]))

    return factory([
        {'source': 'sap:urlbar:SomeCodeHere',
         'type': 'tagged-sap'},
        {'source': 'follow-on:urlbar:SomeCodeHere',
         'type': 'tagged-follow-on'},
        {'source': 'urlbar',
         'type': 'sap'},
    ])


@pytest.fixture()
def expected_search_dashboard_data(define_dataframe_factory):
    # template for the expected results
    factory = define_dataframe_factory(map(to_field, [
        ('submission_date',  '20170101',   StringType(), False),
        ('country',          'DE',         StringType(), True),
        ('locale',          'de',         StringType(), True),
        ('search_cohort',   None,         StringType(), True),
        ('app_version',      '54.0.1',     StringType(), True),
        ('distribution_id',  None,         StringType(), True),
        ('addon_version',    '0.9.5',      StringType(), False),
        ('engine',           'google',     StringType(), False),
        ('source',           'urlbar',     StringType(), False),
        ('tagged-sap',       None,         LongType(), True),
        ('tagged-follow-on', None,         LongType(), True),
        ('sap',              4,            LongType(), True),
    ]))

    return factory([
        {'country': 'US'},
        {'app_version': '52.0.3'},
        {'distribution_id': 'totally not null'},
        {'engine': 'yahoo'},
        {'engine': 'bing'},
        {'sap': 20},
    ])


# Testing functions

def test_explode_search_counts(simple_main_summary,
                               exploded_simple_main_summary,
                               df_equals):
    actual = explode_search_counts(simple_main_summary)

    assert df_equals(actual, exploded_simple_main_summary)


def test_explode_search_counts_bing_absurd(generate_main_summary_data,
                                           generate_exploded_data,
                                           df_equals):
    main_summary_bing_absurd = generate_main_summary_data(
        [
            {'search_counts': [
                generate_search_count(engine='bing', count=10000),
                generate_search_count(engine='yahoo'),
            ]}
        ]
    )

    # expected result only includes yahoo, because the bing entry had an absurd
    # number of searches
    expected = generate_exploded_data([{'engine': 'yahoo'}])

    actual = explode_search_counts(main_summary_bing_absurd)

    assert df_equals(expected, actual)


def test_add_derived_columns(exploded_data_for_derived_cols,
                             derived_columns,
                             df_equals):
    actual = add_derived_columns(exploded_data_for_derived_cols)

    assert df_equals(actual, derived_columns)


def test_basic_aggregation(main_summary,
                           expected_search_dashboard_data,
                           df_equals):
    actual = search_dashboard_etl(main_summary)
    assert df_equals(actual, expected_search_dashboard_data)
