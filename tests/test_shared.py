from _shared import EXPECTED_FILES, table_name_for


def test_strips_olist_prefix_and_dataset_suffix():
    assert table_name_for("olist_order_items_dataset.csv") == "order_items"
    assert table_name_for("olist_customers_dataset.csv") == "customers"


def test_passes_through_filenames_without_olist_wrapping():
    assert (
        table_name_for("product_category_name_translation.csv")
        == "product_category_name_translation"
    )


def test_every_expected_file_maps_to_a_nonempty_table_name():
    for filename in EXPECTED_FILES:
        assert table_name_for(filename)
