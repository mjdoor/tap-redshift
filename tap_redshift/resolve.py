from itertools import dropwhile

import singer
from singer import metadata
from singer.catalog import Catalog, CatalogEntry
from singer.schema import Schema

LOGGER = singer.get_logger()


def desired_columns(selected, table_schema):
    """Return the set of column names we need to include in the SELECT.

    selected - set of column names marked as selected in the input catalog
    table_schema - the most recently discovered Schema for the table
    """
    all_columns = set()
    available = set()
    automatic = set()
    unsupported = set()

    for column, column_schema in table_schema.properties.items():
        all_columns.add(column)
        inclusion = column_schema.inclusion
        if inclusion == 'available':
            available.add(column)
        elif inclusion == 'unsupported':
            unsupported.add(column)
        elif inclusion == 'automatic':
            automatic.add(column)
        else:
            raise Exception('Unknown inclusion ' + inclusion)

    selected_but_unsupported = selected.intersection(unsupported)
    if selected_but_unsupported:
        LOGGER.warning(
            'Columns %s were selected but are not supported. Skipping them.',
            selected_but_unsupported)

    selected_but_nonexistent = selected.difference(all_columns)
    if selected_but_nonexistent:
        LOGGER.warning(
            'Columns %s were selected but do not exist.',
            selected_but_nonexistent)

    return selected.intersection(available).union(automatic)


def entry_is_selected(catalog_entry):
    mdata = metadata.new()
    if catalog_entry.metadata is not None:
        mdata = metadata.to_map(catalog_entry.metadata)
    return bool(catalog_entry.is_selected()
                or metadata.get(mdata, (), 'selected'))


def get_selected_properties(catalog_entry):
    mdata = metadata.to_map(catalog_entry.metadata)
    properties = catalog_entry.schema.properties

    return {
        k for k, v in properties.items()
        if (metadata.get(mdata, ('properties', k), 'selected')
            or (metadata.get(mdata, ('properties', k), 'selected-by-default')
                and metadata.get(mdata, ('properties', k), 'selected') is None)
            or properties[k].selected)}


def resolve_catalog(discovered, catalog, state):
    streams = list(filter(entry_is_selected, catalog.streams))

    currently_syncing = singer.get_currently_syncing(state)
    if currently_syncing:
        streams = dropwhile(
            lambda s: s.tap_stream_id != currently_syncing, streams)

    result = Catalog(streams=[])

    # Return type of discover_catalog has been changed from Catalog class to dict.
    # Cast it back to Catalog class
    discovered = Catalog.from_dict(discovered)
    # Order of columns for each stream
    column_order_map = catalog.column_order_map

    # Iterate over the streams in the input catalog and match each one up
    # with the same stream in the discovered catalog.
    for catalog_entry in streams:
        discovered_table = discovered.get_stream(catalog_entry.tap_stream_id)
        if not discovered_table:
            LOGGER.warning('Database {} table {} selected but does not exist'
                           .format(catalog_entry.database,
                                   catalog_entry.table))
            continue
        selected = get_selected_properties(catalog_entry)

        # These are the columns we need to select
        columns = desired_columns(selected, discovered_table.schema)
        ordered_columns = list(filter(lambda x: x in columns, column_order_map[catalog_entry.stream]))

        schema = Schema(
            type='object',
            properties={col: discovered_table.schema.properties[col]
                        for col in ordered_columns}
        )

        result.streams.append(CatalogEntry(
            tap_stream_id=catalog_entry.tap_stream_id,
            stream=catalog_entry.stream,
            table=catalog_entry.table,
            schema=schema,
            metadata=catalog_entry.metadata
        ))

    return result
