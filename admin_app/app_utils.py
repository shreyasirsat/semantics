import os
from datetime import datetime
from io import StringIO

import pandas as pd
import streamlit as st

from semantic_model_generator.data_processing.proto_utils import (
    proto_to_yaml,
    yaml_to_semantic_model,
)
from semantic_model_generator.generate_model import raw_schema_to_semantic_context
from semantic_model_generator.protos import semantic_model_pb2
from semantic_model_generator.protos.semantic_model_pb2 import Dimension, Table

SNOWFLAKE_ACCOUNT = os.environ["SNOWFLAKE_ACCOUNT_LOCATOR"]
_TMP_FILE_NAME = f"admin_app_temp_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def update_last_validated_model() -> None:
    """Whenever user validated, update the last_validated_model to track semantic_model,
    except for verified_queries field."""
    st.session_state.last_validated_model.CopyFrom(st.session_state.semantic_model)
    # Do not save verfieid_queries field for the latest validated.
    del st.session_state.last_validated_model.verified_queries[:]


def changed_from_last_validated_model() -> bool:
    """Compare the last validated model against latest semantic model,
    except for verified_queries field."""

    for field in st.session_state.semantic_model.DESCRIPTOR.fields:
        if field.name != "verified_queries":
            model_value = getattr(st.session_state.semantic_model, field.name)
            last_validated_value = getattr(
                st.session_state.last_validated_model, field.name
            )
            if model_value != last_validated_value:
                return True
    return False


# TODO:
# 1. Add inline validation of fields and forms.  E.g. if 'SQL Expression' is a required field
#    for adding a dimension then prompt about the missing field in the UI itself.
# 4. Handle error cases in 'Add Table' workflow.
# 5. Add an option to specify connection parameters in the app, instead of env vars.

# Known issues:
# 1. Sometimes the 'Show YAML' and 'Add Table' buttons don't respond to clicks after the user
#    has imported an existing model.
# 2. The semantic model name doesn't update in the headline immediately after user enters it.


def init_session_states() -> None:
    # semantic_model stores the proto of generated semantic model using app.
    if "semantic_model" not in st.session_state:
        st.session_state.semantic_model = semantic_model_pb2.SemanticModel()
    # validated stores the status if the generated yaml has ever been validated.
    if "validated" not in st.session_state:
        st.session_state.validated = False
    # last_validated_model stores the proto (without verfied queries) from last successful validation.
    if "last_validated_model" not in st.session_state:
        st.session_state.last_validated_model = semantic_model_pb2.SemanticModel()

    # initialize session states for the chat page.
    if "messages" not in st.session_state:
        # messages store all chat histories
        st.session_state.messages = []
        # suggestions store suggested questions (if reject to answer) generated by the api during chat.
        st.session_state.suggestions = []
        # active_suggestion stores the active suggestion selected by the user
        st.session_state.active_suggestion = None
        # indicates if the user is editing the generated SQL for the verified query.
        st.session_state.editing = False
        # indicates if the user has confirmed his/her edits for the verified query.
        st.session_state.confirmed_edits = False


@st.experimental_dialog("Edit Dimension")  # type: ignore[misc]
def edit_dimension(table_name: str, dim: semantic_model_pb2.Dimension) -> None:
    """
    Renders a dialog box to edit an existing dimension.
    """
    key_prefix = f"{table_name}-{dim.name}"
    dim.name = st.text_input("Name", dim.name, key=f"{key_prefix}-edit-dim-name")
    dim.expr = st.text_input(
        "SQL Expression", dim.expr, key=f"{key_prefix}-edit-dim-expr"
    )
    dim.description = st.text_area(
        "Description", dim.description, key=f"{key_prefix}-edit-dim-description"
    )
    # Allow users to edit synonyms through a data_editor.
    synonyms_df = st.data_editor(
        pd.DataFrame(list(dim.synonyms), columns=["Synonyms"]),
        num_rows="dynamic",
        key=f"{key_prefix}-edit-dim-synonyms",
    )
    # Store the current values in data_editor in the protobuf.
    del dim.synonyms[:]
    for _, row in synonyms_df.iterrows():
        if row["Synonyms"]:
            dim.synonyms.append(row["Synonyms"])

    # TODO(nsehrawat): Change to a select box with a list of all data types.
    dim.data_type = st.text_input(
        "Data type", dim.data_type, key=f"{key_prefix}-edit-dim-datatype"
    )
    dim.unique = st.checkbox(
        "Does it have unique values?",
        value=dim.unique,
        key=f"{key_prefix}-edit-dim-unique",
    )
    # Allow users to edit sample values through a data_editor.
    sample_values_df = st.data_editor(
        pd.DataFrame(list(dim.sample_values), columns=["Sample Values"]),
        num_rows="dynamic",
        key=f"{key_prefix}-edit-dim-sample-values",
    )
    # Store the current values in data_editor in the protobuf.
    del dim.sample_values[:]
    for _, row in sample_values_df.iterrows():
        if row["Sample Values"]:
            dim.sample_values.append(row["Sample Values"])

    if st.button("Save"):
        st.rerun()


@st.experimental_dialog("Add Dimension")  # type: ignore[misc]
def add_dimension(table: semantic_model_pb2.Table) -> None:
    """
    Renders a dialog box to add a new dimension.
    """
    dim = Dimension()
    dim.name = st.text_input("Name", key=f"{table.name}-add-dim-name")
    dim.expr = st.text_input("SQL Expression", key=f"{table.name}-add-dim-expr")
    dim.description = st.text_area(
        "Description", key=f"{table.name}-add-dim-description"
    )
    synonyms_df = st.data_editor(
        pd.DataFrame(list(dim.synonyms), columns=["Synonyms"]),
        num_rows="dynamic",
        key=f"{table.name}-add-dim-synonyms",
    )
    for _, row in synonyms_df.iterrows():
        if row["Synonyms"]:
            dim.synonyms.append(row["Synonyms"])

    dim.data_type = st.text_input("Data type", key=f"{table.name}-add-dim-datatype")
    dim.unique = st.checkbox(
        "Does it have unique values?", key=f"{table.name}-add-dim-unique"
    )
    sample_values_df = st.data_editor(
        pd.DataFrame(list(dim.sample_values), columns=["Sample Values"]),
        num_rows="dynamic",
        key=f"{table.name}-add-dim-sample-values",
    )
    del dim.sample_values[:]
    for _, row in sample_values_df.iterrows():
        if row["Sample Values"]:
            dim.sample_values.append(row["Sample Values"])

    if st.button("Add"):
        table.dimensions.append(dim)
        st.rerun()


@st.experimental_dialog("Edit Measure")  # type: ignore[misc]
def edit_measure(table_name: str, measure: semantic_model_pb2.Measure) -> None:
    """
    Renders a dialog box to edit an existing measure.
    """
    key_prefix = f"{table_name}-{measure.name}"
    measure.name = st.text_input(
        "Name", measure.name, key=f"{key_prefix}-edit-measure-name"
    )
    measure.expr = st.text_input(
        "SQL Expression", measure.expr, key=f"{key_prefix}-edit-measure-expr"
    )
    measure.description = st.text_area(
        "Description", measure.description, key=f"{key_prefix}-edit-measure-description"
    )
    synonyms_df = st.data_editor(
        pd.DataFrame(list(measure.synonyms), columns=["Synonyms"]),
        num_rows="dynamic",
        key=f"{key_prefix}-edit-measure-synonyms",
    )
    del measure.synonyms[:]
    for _, row in synonyms_df.iterrows():
        if row["Synonyms"]:
            measure.synonyms.append(row["Synonyms"])

    measure.data_type = st.text_input(
        "Data type", measure.data_type, key=f"{key_prefix}-edit-measure-data-type"
    )

    aggr_options = semantic_model_pb2.AggregationType.keys()
    # Replace the 'aggregation_type_unknown' string with an empty string for a better display of options.
    aggr_options[0] = ""
    default_aggregation_idx = next(
        (
            i
            for i, s in enumerate(semantic_model_pb2.AggregationType.values())
            if s == measure.default_aggregation
        ),
        0,
    )

    default_aggregation = st.selectbox(
        "Default Aggregation",
        aggr_options,
        index=default_aggregation_idx,
        key=f"{key_prefix}-edit-measure-default-aggregation",
    )
    if default_aggregation:
        try:
            measure.default_aggregation = semantic_model_pb2.AggregationType.Value(
                default_aggregation
            )  # type: ignore[assignment]
        except ValueError as e:
            st.error(f"Invalid default_aggregation: {e}")
    else:
        measure.default_aggregation = (
            semantic_model_pb2.AggregationType.aggregation_type_unknown
        )

    sample_values_df = st.data_editor(
        pd.DataFrame(list(measure.sample_values), columns=["Sample Values"]),
        num_rows="dynamic",
        key=f"{key_prefix}-edit-measure-sample-values",
    )
    del measure.sample_values[:]
    for _, row in sample_values_df.iterrows():
        if row["Sample Values"]:
            measure.sample_values.append(row["Sample Values"])

    if st.button("Save"):
        st.rerun()


@st.experimental_dialog("Add Measure")  # type: ignore[misc]
def add_measure(table: semantic_model_pb2.Table) -> None:
    """
    Renders a dialog box to add a new measure.
    """
    with st.form(key="add-measure"):
        measure = semantic_model_pb2.Measure()
        measure.name = st.text_input("Name", key=f"{table.name}-add-measure-name")
        measure.expr = st.text_input(
            "SQL Expression", key=f"{table.name}-add-measure-expr"
        )
        measure.description = st.text_area(
            "Description", key=f"{table.name}-add-measure-description"
        )
        synonyms_df = st.data_editor(
            pd.DataFrame(list(measure.synonyms), columns=["Synonyms"]),
            num_rows="dynamic",
            key=f"{table.name}-add-measure-synonyms",
        )
        del measure.synonyms[:]
        for _, row in synonyms_df.iterrows():
            if row["Synonyms"]:
                measure.synonyms.append(row["Synonyms"])

        measure.data_type = st.text_input(
            "Data type", key=f"{table.name}-add-measure-data-type"
        )
        aggr_options = semantic_model_pb2.AggregationType.keys()
        # Replace the 'aggregation_type_unknown' string with an empty string for a better display of options.
        aggr_options[0] = ""
        default_aggregation = st.selectbox(
            "Default Aggregation",
            aggr_options,
            key=f"{table.name}-edit-measure-default-aggregation",
        )
        if default_aggregation:
            try:
                measure.default_aggregation = semantic_model_pb2.AggregationType.Value(
                    default_aggregation
                )  # type: ignore[assignment]
            except ValueError as e:
                st.error(f"Invalid default_aggregation: {e}")

        sample_values_df = st.data_editor(
            pd.DataFrame(list(measure.sample_values), columns=["Sample Values"]),
            num_rows="dynamic",
            key=f"{table.name}-add-measure-sample-values",
        )
        del measure.sample_values[:]
        for _, row in sample_values_df.iterrows():
            if row["Sample Values"]:
                measure.sample_values.append(row["Sample Values"])

        add_button = st.form_submit_button("Add")

    if add_button:
        table.measures.append(measure)
        st.rerun()


@st.experimental_dialog("Edit Time Dimension")  # type: ignore[misc]
def edit_time_dimension(
    table_name: str, tdim: semantic_model_pb2.TimeDimension
) -> None:
    """
    Renders a dialog box to edit a time dimension.
    """
    key_prefix = f"{table_name}-{tdim.name}"
    tdim.name = st.text_input("Name", tdim.name, key=f"{key_prefix}-edit-tdim-name")
    tdim.expr = st.text_input(
        "SQL Expression", tdim.expr, key=f"{key_prefix}-edit-tdim-expr"
    )
    tdim.description = st.text_area(
        "Description",
        tdim.description,
        key=f"{key_prefix}-edit-tdim-description",
    )
    synonyms_df = st.data_editor(
        pd.DataFrame(list(tdim.synonyms), columns=["Synonyms"]),
        num_rows="dynamic",
        key=f"{key_prefix}-tdim-edit-measure-synonyms",
    )
    del tdim.synonyms[:]
    for _, row in synonyms_df.iterrows():
        if row["Synonyms"]:
            tdim.synonyms.append(row["Synonyms"])

    tdim.data_type = st.text_input(
        "Data type", tdim.data_type, key=f"{key_prefix}-edit-tdim-datatype"
    )
    tdim.unique = st.checkbox("Does it have unique values?", value=tdim.unique)
    sample_values_df = st.data_editor(
        pd.DataFrame(list(tdim.sample_values), columns=["Sample Values"]),
        num_rows="dynamic",
        key=f"{key_prefix}-edit-tdim-sample-values",
    )
    del tdim.sample_values[:]
    for _, row in sample_values_df.iterrows():
        if row["Sample Values"]:
            tdim.sample_values.append(row["Sample Values"])

    if st.button("Save"):
        st.rerun()


@st.experimental_dialog("Add Time Dimension")  # type: ignore[misc]
def add_time_dimension(table: semantic_model_pb2.Table) -> None:
    """
    Renders a dialog box to add a new time dimension.
    """
    tdim = semantic_model_pb2.TimeDimension()
    tdim.name = st.text_input("Name", key=f"{table.name}-add-tdim-name")
    tdim.expr = st.text_input("SQL Expression", key=f"{table.name}-add-tdim-expr")
    tdim.description = st.text_area(
        "Description", key=f"{table.name}-add-tdim-description"
    )
    synonyms_df = st.data_editor(
        pd.DataFrame(list(tdim.synonyms), columns=["Synonyms"]),
        num_rows="dynamic",
        key=f"{table.name}-add-tdim-synonyms",
    )
    del tdim.synonyms[:]
    for _, row in synonyms_df.iterrows():
        if row["Synonyms"]:
            tdim.synonyms.append(row["Synonyms"])

    # TODO(nsehrawat): Change the set of allowed data types here.
    tdim.data_type = st.text_input("Data type", key=f"{table.name}-add-tdim-data-types")
    tdim.unique = st.checkbox(
        "Does it have unique values?", key=f"{table.name}-add-tdim-unique"
    )
    sample_values_df = st.data_editor(
        pd.DataFrame(list(tdim.sample_values), columns=["Sample Values"]),
        num_rows="dynamic",
        key=f"{table.name}-add-tdim-sample-values",
    )
    del tdim.sample_values[:]
    for _, row in sample_values_df.iterrows():
        if row["Sample Values"]:
            tdim.sample_values.append(row["Sample Values"])

    if st.button("Add", key=f"{table.name}-add-tdim-add"):
        table.time_dimensions.append(tdim)
        st.rerun()


def delete_dimension(table: semantic_model_pb2.Table, idx: int) -> None:
    """
    Inline deletes the dimension at a particular index in a Table protobuf.
    """
    if len(table.dimensions) < idx:
        return
    del table.dimensions[idx]


def delete_measure(table: semantic_model_pb2.Table, idx: int) -> None:
    """
    Inline deletes the measure at a particular index in a Table protobuf.
    """
    if len(table.measures) < idx:
        return
    del table.measures[idx]


def delete_time_dimension(table: semantic_model_pb2.Table, idx: int) -> None:
    """
    Inline deletes the time dimension at a particular index in a Table protobuf.
    """
    if len(table.time_dimensions) < idx:
        return
    del table.time_dimensions[idx]


def display_table(table_name: str) -> None:
    """
    Display all the data related to a logical table.
    """
    for t in st.session_state.semantic_model.tables:
        if t.name == table_name:
            table: semantic_model_pb2.Table = t
            break

    st.write("#### Table metadata")
    table.name = st.text_input("Table Name", table.name)
    fqn_columns = st.columns(3)
    with fqn_columns[0]:
        table.base_table.database = st.text_input(
            "Physical Database",
            table.base_table.database,
            key=f"{table_name}-base_database",
        )
    with fqn_columns[1]:
        table.base_table.schema = st.text_input(
            "Physical Schema",
            table.base_table.schema,
            key=f"{table_name}-base_schema",
        )
    with fqn_columns[2]:
        table.base_table.table = st.text_input(
            "Physical Table", table.base_table.table, key=f"{table_name}-base_table"
        )

    table.description = st.text_area(
        "Description", table.description, key=f"{table_name}-description"
    )

    synonyms_df = st.data_editor(
        pd.DataFrame(list(table.synonyms), columns=["Synonyms"]),
        num_rows="dynamic",
        key=f"{table_name}-synonyms",
        use_container_width=True,
    )
    del table.synonyms[:]
    for idx, row in synonyms_df.iterrows():
        if row["Synonyms"]:
            table.synonyms.append(row["Synonyms"])

    st.write("#### Dimensions")
    header = ["Name", "Expression", "Data Type"]
    header_cols = st.columns(len(header) + 1)
    for i, h in enumerate(header):
        header_cols[i].write(f"###### {h}")

    for idx, dim in enumerate(table.dimensions):
        cols = st.columns(len(header) + 1)
        cols[0].write(dim.name)
        cols[1].write(dim.expr)
        cols[2].write(dim.data_type)
        with cols[-1]:
            subcols = st.columns(2)
            if subcols[0].button(
                "Edit",
                key=f"{table_name}-edit-dimension-{idx}",
            ):
                edit_dimension(table_name, dim)
            subcols[1].button(
                "Delete",
                key=f"{table_name}-delete-dimension-{idx}",
                on_click=delete_dimension,
                args=(
                    table,
                    idx,
                ),
            )

    if st.button("Add Dimension", key=f"{table_name}-add-dimension"):
        add_dimension(table)

    st.write("#### Measures")
    header_cols = st.columns(len(header) + 1)
    for i, h in enumerate(header):
        header_cols[i].write(f"###### {h}")

    for idx, measure in enumerate(table.measures):
        cols = st.columns(len(header) + 1)
        cols[0].write(measure.name)
        cols[1].write(measure.expr)
        cols[2].write(measure.data_type)
        with cols[-1]:
            subcols = st.columns(2)
            if subcols[0].button("Edit", key=f"{table_name}-edit-measure-{idx}"):
                edit_measure(table_name, measure)
            subcols[1].button(
                "Delete",
                key=f"{table_name}-delete-measure-{idx}",
                on_click=delete_measure,
                args=(
                    table,
                    idx,
                ),
            )

    if st.button("Add Measure", key=f"{table_name}-add-measure"):
        add_measure(table)

    st.write("#### Time Dimensions")
    header_cols = st.columns(len(header) + 1)
    for i, h in enumerate(header):
        header_cols[i].write(f"###### {h}")

    for idx, tdim in enumerate(table.time_dimensions):
        cols = st.columns(len(header) + 1)
        cols[0].write(tdim.name)
        cols[1].write(tdim.expr)
        cols[2].write(tdim.data_type)
        with cols[-1]:
            subcols = st.columns(2)
            if subcols[0].button("Edit", key=f"{table_name}-edit-tdim-{idx}"):
                edit_time_dimension(table_name, tdim)
            subcols[1].button(
                "Delete",
                key=f"{table_name}-delete-tdim-{idx}",
                on_click=delete_time_dimension,
                args=(
                    table,
                    idx,
                ),
            )

    if st.button("Add Time Dimension", key=f"{table_name}-add-tdim"):
        add_time_dimension(table)


@st.experimental_dialog("Add Table")  # type: ignore[misc]
def add_new_table() -> None:
    """
    Renders a dialog box to add a new logical table.
    """
    table = Table()
    table.name = st.text_input("Table Name")
    for t in st.session_state.semantic_model.tables:
        if t.name == table.name:
            st.error(f"Table called '{table.name}' already exists")

    table.base_table.database = st.text_input("Physical Database")
    table.base_table.schema = st.text_input("Physical Schema")
    table.base_table.table = st.text_input("Physical Table")
    st.caption(":gray[Synonyms (hover the table to add new rows!)]")
    synonyms_df = st.data_editor(
        pd.DataFrame(columns=["Synonyms"]),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
    )
    for _, row in synonyms_df.iterrows():
        if row["Synonyms"]:
            table.synonyms.append(row["Synonyms"])
    table.description = st.text_area("Description", key="add-new-table-description")
    if st.button("Add"):
        with st.spinner(text="Fetching table details from database ..."):
            try:
                semantic_model = raw_schema_to_semantic_context(
                    base_tables=[
                        f"{table.base_table.database}.{table.base_table.schema}.{table.base_table.table}"
                    ],
                    snowflake_account=SNOWFLAKE_ACCOUNT,
                    semantic_model_name="foo",  # A placeholder name that's not used anywhere.
                )
            except Exception as ex:
                st.error(f"Error adding table: {ex}")
                return
            table.dimensions.extend(semantic_model.tables[0].dimensions)
            table.measures.extend(semantic_model.tables[0].measures)
            table.time_dimensions.extend(semantic_model.tables[0].time_dimensions)
            for t in st.session_state.semantic_model.tables:
                if t.name == table.name:
                    st.error(f"Table called '{table.name}' already exists")
                    return
        st.session_state.semantic_model.tables.append(table)
        st.rerun()


def display_semantic_model() -> None:
    """
    Renders the entire semantic model.
    """
    semantic_model = st.session_state.semantic_model
    semantic_model.name = st.text_input("Name", semantic_model.name)
    semantic_model.description = st.text_area(
        "Description",
        semantic_model.description,
        key="display-semantic-model-description",
    )


def edit_semantic_model() -> None:
    st.write("### Tables")
    for t in st.session_state.semantic_model.tables:
        with st.expander(t.name):
            display_table(t.name)
    if st.button("Add Table"):
        add_new_table()


def import_yaml() -> None:
    """
    Renders a page to import an existing yaml file.
    """
    uploaded_file = st.file_uploader(
        "Choose a semantic model YAML file",
        type=[".yaml", ".yml"],
        accept_multiple_files=False,
    )
    pb: semantic_model_pb2.SemanticModel | None = None

    if uploaded_file is not None:
        try:
            yaml_str = StringIO(uploaded_file.getvalue().decode("utf-8")).read()
            pb = yaml_to_semantic_model(yaml_str)
        except Exception as ex:
            st.error(f"Failed to import: {ex}")
            return
        if pb is None:
            st.error("Failed to import, did you choose a file?")
            return

        st.session_state["semantic_model"] = pb
        st.success(f"Successfully imported {pb.name}!", icon="✅")
        if "yaml_just_imported" not in st.session_state:
            st.session_state["yaml_just_imported"] = True
            st.rerun()


@st.experimental_dialog("Model YAML", width="large")  # type: ignore
def show_yaml_in_dialog() -> None:
    yaml = proto_to_yaml(st.session_state.semantic_model)
    st.code(
        yaml,
        language="yaml",
        line_numbers=True,
    )


def upload_yaml(file_name: str) -> None:
    """util to upload the semantic model."""
    import os
    import tempfile

    from semantic_model_generator.snowflake_utils.snowflake_connector import (
        SnowflakeConnector,
    )

    connector = SnowflakeConnector(
        account_name=SNOWFLAKE_ACCOUNT,
        max_workers=1,
    )
    yaml = proto_to_yaml(st.session_state.semantic_model)

    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_file_path = os.path.join(temp_dir, f"{file_name}.yaml")

        with open(tmp_file_path, "w") as temp_file:
            temp_file.write(yaml)

        with connector.connect(
            db_name=st.session_state.snowflake_stage.stage_database,
            schema_name=st.session_state.snowflake_stage.stage_schema,
        ) as conn:
            upload_sql = f"PUT file://{tmp_file_path} @{st.session_state.snowflake_stage.stage_name} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
            conn.cursor().execute(upload_sql)

            if file_name != _TMP_FILE_NAME:
                # If the user did official uploading, delete the saved temp file from stage.
                try:
                    delete_tmp_sql = f"REMOVE @{st.session_state.snowflake_stage.stage_name}/{_TMP_FILE_NAME}.yaml"
                    conn.cursor().execute(delete_tmp_sql)
                except Exception:
                    pass


def validate_and_upload_tmp_yaml() -> None:
    """
    Validate the semantic model.
    If successfully validated, upload a temp file into stage, to allow chatting and adding VQR against it.
    """
    from semantic_model_generator.validate_model import validate

    yaml_str = proto_to_yaml(st.session_state.semantic_model)
    try:
        # whenever valid, upload to temp stage path.
        validate(yaml_str, SNOWFLAKE_ACCOUNT)
        upload_yaml(_TMP_FILE_NAME)
        st.session_state.validated = True
        update_last_validated_model()
        st.success("Successfully validated your model!")
    except Exception as e:
        st.warning(f"Invalid YAML: {e} please fix!")


@st.experimental_dialog("Upload YAML to stage")  # type: ignore[misc]
def user_upload_yaml() -> None:
    """
    Allow user to input a file_name and upload the file to stage accordingly.
    Auto-revalidate the model if detects any semantic model changes since last validation.
    """
    if changed_from_last_validated_model():
        st.info(
            "Your semantic model has changed since last validation. Re-validating before uploading...."
        )
        validate_and_upload_tmp_yaml()

    st.session_state.file_name = st.text_input("Enter the file name to upload:")
    if st.button("Submit Upload"):
        st.write(
            f"Uploading into @{st.session_state.snowflake_stage.stage_name}/{st.session_state.file_name}.yaml"
        )
        upload_yaml(st.session_state.file_name)
        st.success(
            f"Uploaded @{st.session_state.snowflake_stage.stage_name}/{st.session_state.file_name}.yaml!"
        )


def semantic_model_exists() -> bool:
    if "semantic_model" in st.session_state:
        if hasattr(st.session_state.semantic_model, "name"):
            if isinstance(st.session_state.semantic_model.name, str):
                model_name: str = st.session_state.semantic_model.name.strip()
                return model_name != ""
    return False
