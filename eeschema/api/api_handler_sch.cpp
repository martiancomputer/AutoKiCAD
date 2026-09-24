/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright (C) 2024 Jon Evans <jon@craftyjon.com>
 * Copyright The KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#include <api/api_handler_sch.h>
#include <api/api_enums.h>
#include <api/api_sch_utils.h>
#include <api/api_utils.h>
#include <api/sch_context.h>
#include <magic_enum.hpp>
#include <base_screen.h>
#include <jobs/job_export_bom.h>
#include <jobs/job_export_sch_netlist.h>
#include <jobs/job_export_sch_plot.h>
#include <kiway.h>
#include <sch_field.h>
#include <sch_group.h>
#include <connection_graph.h>
#include <sch_commit.h>
#include <lib_symbol.h>
#include <erc/erc.h>
#include <erc/erc_report.h>
#include <erc/erc_settings.h>
#include <erc/erc_item.h>
#include <drawing_sheet/ds_proxy_view_item.h>
#include <kiway.h>
#include <base_units.h>
#include <wx/tokenzr.h>
#include <ranges>
#include <sch_edit_frame.h>
#include <sch_label.h>
#include <sch_screen.h>
#include <sch_sheet.h>
#include <sch_sheet_path.h>
#include <sch_sheet_pin.h>
#include <sch_symbol.h>
#include <schematic.h>
#include <tool/actions.h>
#include <tool/tool_manager.h>
#include <tools/sch_selection_tool.h>
#include <project.h>
#include <project_sch.h>
#include <libraries/symbol_library_adapter.h>
#include <pgm_base.h>
#include <sch_commit.h>
#include <wildcards_and_files_ext.h>
#include <wx/filename.h>

#include <api/common/types/base_types.pb.h>

using namespace kiapi::common::commands;
using kiapi::common::types::CommandStatus;
using kiapi::common::types::DocumentType;
using kiapi::common::types::ItemRequestStatus;


std::set<KICAD_T> API_HANDLER_SCH::s_allowedTypes = {
    // SCH_MARKER_T,
    SCH_JUNCTION_T,
    SCH_NO_CONNECT_T,
    SCH_BUS_WIRE_ENTRY_T,
    SCH_BUS_BUS_ENTRY_T,
    SCH_LINE_T,
    SCH_SHAPE_T,
    SCH_BITMAP_T,
    SCH_TEXTBOX_T,
    SCH_TEXT_T,
    // SCH_TABLE_T,
    SCH_LABEL_T,
    SCH_GLOBAL_LABEL_T,
    SCH_GROUP_T,
    SCH_HIER_LABEL_T,
    SCH_DIRECTIVE_LABEL_T,
    SCH_SYMBOL_T,
    SCH_SHEET_T,
};


HANDLER_RESULT<types::RunJobResponse> ExecuteSchematicJob( KIWAY* aKiway, JOB& aJob )
{
    types::RunJobResponse response;
    WX_STRING_REPORTER reporter;
    int exitCode = aKiway->ProcessJob( KIWAY::FACE_SCH, &aJob, &reporter );

    for( const JOB_OUTPUT& output : aJob.GetOutputs() )
        response.add_output_path( output.m_outputPath.ToUTF8() );

    if( exitCode == 0 )
    {
        response.set_status( types::JobStatus::JS_SUCCESS );
        return response;
    }

    response.set_status( types::JobStatus::JS_ERROR );
    response.set_message( fmt::format( "Schematic export job '{}' failed with exit code {}: {}",
                                       aJob.GetType(), exitCode,
                                       reporter.GetMessages().ToStdString() ) );
    return response;
}


API_HANDLER_SCH::API_HANDLER_SCH( SCH_EDIT_FRAME* aFrame ) :
        API_HANDLER_SCH( CreateSchFrameContext( aFrame ), aFrame )
{
}


API_HANDLER_SCH::API_HANDLER_SCH( std::shared_ptr<SCH_CONTEXT> aContext,
                                  SCH_EDIT_FRAME* aFrame ) :
        API_HANDLER_EDITOR( aFrame ),
        m_frame( aFrame ),
        m_context( std::move( aContext ) )
{
    using namespace kiapi::schematic::jobs;
    using namespace kiapi::schematic::types;

    registerHandler<GetOpenDocuments, GetOpenDocumentsResponse>(
            &API_HANDLER_SCH::handleGetOpenDocuments );
    registerHandler<SaveDocument, google::protobuf::Empty>(
            &API_HANDLER_SCH::handleSaveDocument );
    registerHandler<SaveCopyOfDocument, google::protobuf::Empty>(
            &API_HANDLER_SCH::handleSaveCopyOfDocument );

    registerHandler<GetItems, GetItemsResponse>( &API_HANDLER_SCH::handleGetItems );
    registerHandler<GetItemsById, GetItemsResponse>( &API_HANDLER_SCH::handleGetItemsById );

    registerHandler<GetSelection, SelectionResponse>( &API_HANDLER_SCH::handleGetSelection );
    registerHandler<ClearSelection, Empty>( &API_HANDLER_SCH::handleClearSelection );
    registerHandler<AddToSelection, SelectionResponse>( &API_HANDLER_SCH::handleAddToSelection );
    registerHandler<RemoveFromSelection, SelectionResponse>(
            &API_HANDLER_SCH::handleRemoveFromSelection );

    registerHandler<RunSchematicJobExportSvg, types::RunJobResponse>(
            &API_HANDLER_SCH::handleRunSchematicJobExportSvg );
    registerHandler<RunSchematicJobExportDxf, types::RunJobResponse>(
            &API_HANDLER_SCH::handleRunSchematicJobExportDxf );
    registerHandler<RunSchematicJobExportPdf, types::RunJobResponse>(
            &API_HANDLER_SCH::handleRunSchematicJobExportPdf );
    registerHandler<RunSchematicJobExportPs, types::RunJobResponse>(
            &API_HANDLER_SCH::handleRunSchematicJobExportPs );
    registerHandler<RunSchematicJobExportNetlist, types::RunJobResponse>(
            &API_HANDLER_SCH::handleRunSchematicJobExportNetlist );
    registerHandler<RunSchematicJobExportBOM, types::RunJobResponse>(
            &API_HANDLER_SCH::handleRunSchematicJobExportBOM );
    registerHandler<GetSchematicHierarchy, SchematicHierarchyResponse>( &API_HANDLER_SCH::handleGetSchematicHierarchy );
    registerHandler<GetPageSettings, types::PageSettings>( &API_HANDLER_SCH::handleGetPageSettings );
    registerHandler<SetPageSettings, types::PageSettings>( &API_HANDLER_SCH::handleSetPageSettings );
    registerHandler<GetSchematicNetlist, SchematicNetlistResponse>( &API_HANDLER_SCH::handleGetSchematicNetlist );
    registerHandler<kiapi::schematic::types::PlaceSymbol, kiapi::schematic::types::PlaceSymbolResponse>( &API_HANDLER_SCH::handlePlaceSymbol );
    registerHandler<kiapi::schematic::types::SearchSymbols,
                    kiapi::schematic::types::SearchSymbolsResponse>(
            &API_HANDLER_SCH::handleSearchSymbols );
    registerHandler<kiapi::schematic::types::RunErc,
                    kiapi::schematic::types::RunErcResponse>(
            &API_HANDLER_SCH::handleRunErc );
}


std::unique_ptr<COMMIT> API_HANDLER_SCH::createCommit()
{
    if( m_frame )
        return std::make_unique<SCH_COMMIT>( m_frame );

    return std::make_unique<SCH_COMMIT>( toolManager() );
}


SCHEMATIC* API_HANDLER_SCH::schematic() const
{
    wxCHECK( m_context, nullptr );
    return m_context->GetSchematic();
}


std::optional<ApiResponseStatus> API_HANDLER_SCH::checkForHeadless( const std::string& aCommandName ) const
{
    if( m_frame )
        return std::nullopt;

    ApiResponseStatus e;
    e.set_status( ApiStatusCode::AS_UNIMPLEMENTED );
    e.set_error_message( fmt::format( "{} is not available in headless mode", aCommandName ) );
    return e;
}


bool API_HANDLER_SCH::packSchItem( google::protobuf::Any& aOut, SCH_ITEM* aItem,
                                   const SCH_SHEET_PATH& aPath )
{
    if( aItem->Type() == SCH_SYMBOL_T )
    {
        kiapi::schematic::types::SchematicSymbolInstance symbol;

        if( !PackSymbol( &symbol, static_cast<SCH_SYMBOL*>( aItem ), aPath ) )
            return false;

        aOut.PackFrom( symbol );
    }
    else if( aItem->Type() == SCH_SHEET_T )
    {
        kiapi::schematic::types::SheetSymbol sheet;

        if( !PackSheet( &sheet, static_cast<SCH_SHEET*>( aItem ), aPath ) )
            return false;

        aOut.PackFrom( sheet );
    }
    else
    {
        aItem->Serialize( aOut );
    }

    return true;
}


std::optional<SCH_ITEM*> API_HANDLER_SCH::getItemById( const KIID& aId, SCH_SHEET_PATH* aPathOut ) const
{
    if( !schematic()->HasHierarchy() )
        schematic()->RefreshHierarchy();

    SCH_ITEM* item = schematic()->ResolveItem( aId, aPathOut, true );

    if( !item )
        return std::nullopt;

    return item;
}


tl::expected<bool, ApiResponseStatus>
API_HANDLER_SCH::validateDocumentInternal( const DocumentSpecifier& aDocument ) const
{
    if( aDocument.type() != DocumentType::DOCTYPE_SCHEMATIC )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "the requested document is not a schematic" );
        return tl::unexpected( e );
    }

    const PROJECT& prj = m_context->Prj();

    if( aDocument.project().name().compare( prj.GetProjectName().ToUTF8() ) != 0 )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "the requested project {} is not open",
                                          aDocument.project().name() ) );
        return tl::unexpected( e );
    }

    if( aDocument.project().path().compare( prj.GetProjectPath().ToUTF8() ) != 0 )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "the requested project {} is not open at path {}",
                                          aDocument.project().name(),
                                          aDocument.project().path() ) );
        return tl::unexpected( e );
    }

    if( aDocument.has_sheet_path() )
    {
        KIID_PATH path = UnpackSheetPath( aDocument.sheet_path() );

        if( !schematic()->Hierarchy().HasPath( path ) )
        {
            ApiResponseStatus e;
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message( fmt::format( "the requested sheet path {} is not valid for this schematic",
                                              path.AsString().ToStdString() ) );
            return tl::unexpected( e );
        }
    }

    return true;
}


HANDLER_RESULT<google::protobuf::Empty> API_HANDLER_SCH::handleSaveDocument( const HANDLER_CONTEXT<SaveDocument>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    if( !context()->SaveSchematic() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "failed to save schematic" );
        return tl::unexpected( e );
    }

    return google::protobuf::Empty();
}


HANDLER_RESULT<google::protobuf::Empty>
API_HANDLER_SCH::handleSaveCopyOfDocument( const HANDLER_CONTEXT<SaveCopyOfDocument>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    wxFileName schematicPath( project().AbsolutePath( wxString::FromUTF8( aCtx.Request.path() ) ) );

    if( !schematicPath.IsOk() || !schematicPath.IsDirWritable() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message(
                fmt::format( "save path '{}' could not be opened", schematicPath.GetFullPath().ToStdString() ) );
        return tl::unexpected( e );
    }

    if( schematicPath.FileExists() && ( !schematicPath.IsFileWritable() || !aCtx.Request.options().overwrite() ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "save path '{}' exists and cannot be overwritten",
                                          schematicPath.GetFullPath().ToStdString() ) );
        return tl::unexpected( e );
    }

    if( schematicPath.GetExt() != FILEEXT::KiCadSchematicFileExtension )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "save path '{}' must have a kicad_sch extension",
                                          schematicPath.GetFullPath().ToStdString() ) );
        return tl::unexpected( e );
    }

    bool includeProject = true;

    if( aCtx.Request.has_options() )
        includeProject = aCtx.Request.options().include_project();

    if( !context()->SaveSchematicCopy( schematicPath.GetFullPath(), includeProject ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "failed to save schematic copy" );
        return tl::unexpected( e );
    }

    return google::protobuf::Empty();
}


HANDLER_RESULT<GetOpenDocumentsResponse> API_HANDLER_SCH::handleGetOpenDocuments(
        const HANDLER_CONTEXT<GetOpenDocuments>& aCtx )
{
    if( aCtx.Request.type() != DocumentType::DOCTYPE_SCHEMATIC )
    {
        ApiResponseStatus e;

        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    GetOpenDocumentsResponse response;
    common::types::DocumentSpecifier doc;

    wxFileName fn( m_context->GetCurrentFileName() );

    doc.set_type( DocumentType::DOCTYPE_SCHEMATIC );

    if( std::optional<SCH_SHEET_PATH> path = m_context->GetCurrentSheet() )
        PackSheetPath( *doc.mutable_sheet_path(), path->Path() );

    PackProject( *doc.mutable_project(), m_context->Prj() );

    response.mutable_documents()->Add( std::move( doc ) );
    return response;
}


void API_HANDLER_SCH::filterValidSchTypes( std::set<KICAD_T>& aTypeList )
{
    std::erase_if( aTypeList,
                   []( KICAD_T aType )
                   {
                       return !s_allowedTypes.contains( aType );
                   } );
}


HANDLER_RESULT<GetItemsResponse> API_HANDLER_SCH::handleGetItems( const HANDLER_CONTEXT<GetItems>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    if( HANDLER_RESULT<std::optional<KIID>> valid = validateItemHeaderDocument( aCtx.Request.header() );
        !valid.has_value() )
    {
        return tl::unexpected( valid.error() );
    }

    std::set<KICAD_T> typesRequested, typesInserted;

    for( KICAD_T type : parseRequestedItemTypes( aCtx.Request.types() ) )
        typesRequested.insert( type );

    filterValidSchTypes( typesRequested );

    if( typesRequested.empty() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "none of the requested types are valid for a Schematic object" );
        return tl::unexpected( e );
    }

    SCH_SHEET_LIST hierarchy = schematic()->Hierarchy();
    std::optional<SCH_SHEET_PATH> pathFilter;

    if( aCtx.Request.header().document().has_sheet_path() )
    {
        KIID_PATH kp = UnpackSheetPath( aCtx.Request.header().document().sheet_path() );
        pathFilter = hierarchy.GetSheetPathByKIIDPath( kp );
    }

    std::map<KICAD_T, std::vector<std::pair<EDA_ITEM*, SCH_SHEET_PATH>>> itemMap;

    auto processScreen =
        [&]( const SCH_SHEET_PATH& aPath )
        {
            const SCH_SCREEN* aScreen = aPath.LastScreen();

            for( SCH_ITEM* aItem : aScreen->Items() )
            {
                itemMap[ aItem->Type() ].emplace_back( aItem, aPath );

                aItem->RunOnChildren(
                        [&]( SCH_ITEM* aChild )
                        {
                            itemMap[ aChild->Type() ].emplace_back( aChild, aPath );
                        },
                        RECURSE_MODE::NO_RECURSE );
            }
        };

    if( pathFilter )
    {
        processScreen( *pathFilter );
    }
    else
    {
        for( const SCH_SHEET_PATH& path : hierarchy )
            processScreen( path );
    }

    GetItemsResponse response;
    google::protobuf::Any any;

    for( KICAD_T type : parseRequestedItemTypes( aCtx.Request.types() ) )
    {
        if( !s_allowedTypes.contains( type ) )
            continue;

        if( typesInserted.contains( type ) )
            continue;

        for( const auto& [item, itemPath] : itemMap[type] )
        {
            if( packSchItem( any, static_cast<SCH_ITEM*>( item ), itemPath ) )
                response.mutable_items()->Add( std::move( any ) );
        }
    }

    response.set_status( ItemRequestStatus::IRS_OK );
    return response;
}


HANDLER_RESULT<GetItemsResponse> API_HANDLER_SCH::handleGetItemsById( const HANDLER_CONTEXT<GetItemsById>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    if( !validateItemHeaderDocument( aCtx.Request.header() ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    SCH_SHEET_LIST hierarchy = schematic()->Hierarchy();
    std::optional<SCH_SHEET_PATH> pathFilter;

    if( aCtx.Request.header().document().has_sheet_path() )
    {
        KIID_PATH kp = UnpackSheetPath( aCtx.Request.header().document().sheet_path() );
        pathFilter = hierarchy.GetSheetPathByKIIDPath( kp );
    }

    GetItemsResponse response;
    SCH_ITEM* item = nullptr;
    google::protobuf::Any any;

    for( const types::KIID& idProto : aCtx.Request.items() )
    {
        KIID id( idProto.value() );

        SCH_SHEET_PATH itemPath;

        if( pathFilter )
        {
            item = pathFilter->ResolveItem( id );
            itemPath = *pathFilter;
        }
        else
        {
            item = hierarchy.ResolveItem( id, &itemPath, true );
        }

        if( !item || !s_allowedTypes.contains( item->Type() ) )
            continue;

        if( item->Type() == SCH_SYMBOL_T )
        {
            kiapi::schematic::types::SchematicSymbolInstance symbol;

            if( !PackSymbol( &symbol, static_cast<SCH_SYMBOL*>( item ), itemPath ) )
                continue;

            any.PackFrom( symbol );
        }
        else if( item->Type() == SCH_SHEET_T )
        {
            kiapi::schematic::types::SheetSymbol sheet;

            if( !PackSheet( &sheet, static_cast<SCH_SHEET*>( item ), itemPath ) )
                continue;

            any.PackFrom( sheet );
        }
        else
        {
            item->Serialize( any );
        }

        response.mutable_items()->Add( std::move( any ) );
    }

    if( response.items().empty() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "none of the requested IDs were found or valid" );
        return tl::unexpected( e );
    }

    response.set_status( ItemRequestStatus::IRS_OK );
    return response;
}


HANDLER_RESULT<SelectionResponse>
API_HANDLER_SCH::handleGetSelection( const HANDLER_CONTEXT<GetSelection>& aCtx )
{
    if( std::optional<ApiResponseStatus> headless = checkForHeadless( "GetSelection" ) )
        return tl::unexpected( *headless );

    if( !validateItemHeaderDocument( aCtx.Request.header() ) )
    {
        ApiResponseStatus e;
        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    std::set<KICAD_T> filter;

    for( KICAD_T type : parseRequestedItemTypes( aCtx.Request.types() ) )
        filter.insert( type );

    SCH_SELECTION_TOOL* tool = m_context->GetToolManager()->GetTool<SCH_SELECTION_TOOL>();
    SCH_SHEET_PATH path = m_context->GetCurrentSheet().value_or( SCH_SHEET_PATH() );

    SelectionResponse response;
    google::protobuf::Any any;

    for( EDA_ITEM* item : tool->GetSelection() )
    {
        if( filter.empty() || filter.contains( item->Type() ) )
        {
            if( packSchItem( any, static_cast<SCH_ITEM*>( item ), path ) )
                response.mutable_items()->Add( std::move( any ) );
        }
    }

    return response;
}


HANDLER_RESULT<Empty>
API_HANDLER_SCH::handleClearSelection( const HANDLER_CONTEXT<ClearSelection>& aCtx )
{
    if( std::optional<ApiResponseStatus> headless = checkForHeadless( "ClearSelection" ) )
        return tl::unexpected( *headless );

    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    if( !validateItemHeaderDocument( aCtx.Request.header() ) )
    {
        ApiResponseStatus e;
        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    m_context->GetToolManager()->RunAction( ACTIONS::selectionClear );
    m_frame->Refresh();

    return Empty();
}


HANDLER_RESULT<SelectionResponse>
API_HANDLER_SCH::handleAddToSelection( const HANDLER_CONTEXT<AddToSelection>& aCtx )
{
    if( std::optional<ApiResponseStatus> headless = checkForHeadless( "AddToSelection" ) )
        return tl::unexpected( *headless );

    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    if( !validateItemHeaderDocument( aCtx.Request.header() ) )
    {
        ApiResponseStatus e;
        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    SCH_SELECTION_TOOL* tool = m_context->GetToolManager()->GetTool<SCH_SELECTION_TOOL>();
    SCH_SHEET_PATH current = m_context->GetCurrentSheet().value_or( SCH_SHEET_PATH() );

    EDA_ITEMS toAdd;

    for( const types::KIID& id : aCtx.Request.items() )
    {
        SCH_SHEET_PATH itemPath;

        // Selection only operates on the currently-displayed sheet; off-sheet items are skipped
        if( std::optional<SCH_ITEM*> item = getItemById( KIID( id.value() ), &itemPath );
            item && itemPath == current )
        {
            toAdd.push_back( *item );
        }
    }

    tool->AddItemsToSel( &toAdd );
    m_frame->Refresh();

    SelectionResponse response;
    google::protobuf::Any any;

    for( EDA_ITEM* item : tool->GetSelection() )
    {
        if( packSchItem( any, static_cast<SCH_ITEM*>( item ), current ) )
            response.mutable_items()->Add( std::move( any ) );
    }

    return response;
}


HANDLER_RESULT<SelectionResponse>
API_HANDLER_SCH::handleRemoveFromSelection( const HANDLER_CONTEXT<RemoveFromSelection>& aCtx )
{
    if( std::optional<ApiResponseStatus> headless = checkForHeadless( "RemoveFromSelection" ) )
        return tl::unexpected( *headless );

    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    if( !validateItemHeaderDocument( aCtx.Request.header() ) )
    {
        ApiResponseStatus e;
        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }

    SCH_SELECTION_TOOL* tool = m_context->GetToolManager()->GetTool<SCH_SELECTION_TOOL>();
    SCH_SHEET_PATH current = m_context->GetCurrentSheet().value_or( SCH_SHEET_PATH() );

    EDA_ITEMS toRemove;

    for( const types::KIID& id : aCtx.Request.items() )
    {
        SCH_SHEET_PATH itemPath;

        if( std::optional<SCH_ITEM*> item = getItemById( KIID( id.value() ), &itemPath );
            item && itemPath == current )
        {
            toRemove.push_back( *item );
        }
    }

    tool->RemoveItemsFromSel( &toRemove );
    m_frame->Refresh();

    SelectionResponse response;
    google::protobuf::Any any;

    for( EDA_ITEM* item : tool->GetSelection() )
    {
        if( packSchItem( any, static_cast<SCH_ITEM*>( item ), current ) )
            response.mutable_items()->Add( std::move( any ) );
    }

    return response;
}


HANDLER_RESULT<std::unique_ptr<EDA_ITEM>> API_HANDLER_SCH::createItemForType( KICAD_T aType, EDA_ITEM* aContainer )
{
    if( !aContainer )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "Tried to create an item in a null container" );
        return tl::unexpected( e );
    }

    if( !s_allowedTypes.contains( aType ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "type {} is not supported by the schematic API handler",
                                          magic_enum::enum_name( aType ) ) );
        return tl::unexpected( e );
    }

    if( aType == SCH_PIN_T && !dynamic_cast<SCH_SYMBOL*>( aContainer ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "Tried to create a pin in {}, which is not a symbol",
                                          aContainer->GetFriendlyName().ToStdString() ) );
        return tl::unexpected( e );
    }
    else if( aType == SCH_SHEET_T && !dynamic_cast<SCH_SCREEN*>( aContainer ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "Tried to create a sheet symbol in {}, which is not a "
                                          "schematic sheet",
                                          aContainer->GetFriendlyName().ToStdString() ) );
        return tl::unexpected( e );
    }
    else if( aType == SCH_SYMBOL_T && !dynamic_cast<SCH_SCREEN*>( aContainer ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "Tried to create a symbol in {}, which is not a "
                                          "schematic sheet",
                                          aContainer->GetFriendlyName().ToStdString() ) );
        return tl::unexpected( e );
    }

    std::unique_ptr<EDA_ITEM> created = CreateItemForType( aType, aContainer );

    if( created && !created->GetParent() )
        created->SetParent( aContainer );

    if( !created )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "Tried to create an item of type {}, which is unhandled",
                                          magic_enum::enum_name( aType ) ) );
        return tl::unexpected( e );
    }

    return created;
}


HANDLER_RESULT<ItemRequestStatus> API_HANDLER_SCH::handleCreateUpdateItemsInternal( bool aCreate,
        const std::string& aClientName,
        const types::ItemHeader &aHeader,
        const google::protobuf::RepeatedPtrField<google::protobuf::Any>& aItems,
        std::function<void( ItemStatus, google::protobuf::Any )> aItemHandler )
{
    ApiResponseStatus e;

    auto containerResult = validateItemHeaderDocument( aHeader );

    if( !containerResult && containerResult.error().status() == ApiStatusCode::AS_UNHANDLED )
    {
        // No message needed for AS_UNHANDLED; this is an internal flag for the API server
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        return tl::unexpected( e );
    }
    else if( !containerResult )
    {
        e.CopyFrom( containerResult.error() );
        return tl::unexpected( e );
    }

    SCH_SHEET_LIST hierarchy = schematic()->Hierarchy();
    SCH_SCREEN* targetScreen = schematic()->GetCurrentScreen();
    SCH_SHEET_PATH targetPath = m_context->GetCurrentSheet().value_or( *hierarchy.begin() );

    if( aHeader.document().has_sheet_path() )
    {
        KIID_PATH kp = UnpackSheetPath( aHeader.document().sheet_path() );
        if( std::optional<SCH_SHEET_PATH> path = hierarchy.GetSheetPathByKIIDPath( kp ) )
        {
            targetPath = *path;
            targetScreen = targetPath.LastScreen();
        }
    }

    SCH_COMMIT* commit = static_cast<SCH_COMMIT*>( getCurrentCommit( aClientName ) );
    bool connectivityChanged = false;   // an in-place symbol update invalidated the net graph

    for( const google::protobuf::Any& anyItem : aItems )
    {
        ItemStatus status;
        std::optional<KICAD_T> type = TypeNameFromAny( anyItem );

        if( !type )
        {
            status.set_code( ItemStatusCode::ISC_INVALID_TYPE );
            status.set_error_message( fmt::format( "Could not decode a valid type from {}",
                                                   anyItem.type_url() ) );
            aItemHandler( status, anyItem );
            continue;
        }

        EDA_ITEM* container = targetScreen;

        HANDLER_RESULT<std::unique_ptr<EDA_ITEM>> creationResult = createItemForType( *type, container );

        if( !creationResult )
        {
            status.set_code( ItemStatusCode::ISC_INVALID_TYPE );
            status.set_error_message( creationResult.error().error_message() );
            aItemHandler( status, anyItem );
            continue;
        }

        std::unique_ptr<EDA_ITEM> item( std::move( *creationResult ) );

        bool unpacked = false;

        if( *type == SCH_SYMBOL_T )
        {
            kiapi::schematic::types::SchematicSymbolInstance symbol;
            unpacked = anyItem.UnpackTo( &symbol )
                       && UnpackSymbol( static_cast<SCH_SYMBOL*>( item.get() ), symbol );
        }
        else if( *type == SCH_SHEET_T )
        {
            kiapi::schematic::types::SheetSymbol sheetProto;
            unpacked = anyItem.UnpackTo( &sheetProto );

            if( unpacked )
            {
                SCH_SHEET* sheet = static_cast<SCH_SHEET*>( item.get() );

                if( tl::expected<bool, ApiResponseStatus> result = UnpackSheet( sheet, sheetProto );
                    result.has_value() )
                {
                    unpacked = *result;
                    SCH_SHEET_INSTANCE instance;

                    if( !sheet->GetInstances().empty() )
                        instance = *sheet->GetInstances().begin();

                    if( instance.m_PageNumber.IsEmpty() )
                        instance.m_PageNumber = hierarchy.GetNextPageNumber();

                    if( instance.m_Path.empty() )
                    {
                        SCH_SHEET_PATH newPath( targetPath );
                        newPath.push_back( sheet );
                        instance.m_Path = newPath.Path();
                    }

                    sheet->AddInstance( instance );
                }
                else
                {
                    return tl::unexpected( result.error() );
                }
            }
        }
        else
        {
            unpacked = item->Deserialize( anyItem );
        }

        if( !unpacked )
        {
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message( fmt::format( "could not unpack {} from request",
                                              item->GetClass().ToStdString() ) );
            return tl::unexpected( e );
        }

        SCH_ITEM* existingItem = nullptr;
        SCH_SHEET_PATH existingPath;

        existingItem = targetPath.ResolveItem( item->m_Uuid );

        if( existingItem )
            existingPath = targetPath;

        if( aCreate && existingItem )
        {
            status.set_code( ItemStatusCode::ISC_EXISTING );
            status.set_error_message( fmt::format( "an item with UUID {} already exists",
                                                   item->m_Uuid.AsStdString() ) );
            aItemHandler( status, anyItem );
            continue;
        }
        else if( !aCreate && !existingItem )
        {
            status.set_code( ItemStatusCode::ISC_NONEXISTENT );
            status.set_error_message( fmt::format( "an item with UUID {} does not exist",
                                                   item->m_Uuid.AsStdString() ) );
            aItemHandler( status, anyItem );
            continue;
        }

        if( !aCreate )
        {
            SCH_SCREEN* itemScreen = existingPath.LastScreen();

            if( itemScreen != targetScreen )
            {
                status.set_code( ItemStatusCode::ISC_INVALID_DATA );
                status.set_error_message( fmt::format( "item {} exists on a different sheet than targeted",
                                                       item->m_Uuid.AsStdString() ) );
                aItemHandler( status, anyItem );
                continue;
            }
        }

        if( *type == SCH_SHEET_T )
        {
            SCH_SHEET* sheet = static_cast<SCH_SHEET*>( item.get() );

            if( aCreate && !sheet->GetScreen() )
                sheet->SetScreen( new SCH_SCREEN( schematic() ) );

            SCH_SHEET_PATH parentPath;

            if( aCreate )
                parentPath = targetPath;
            else
                parentPath = existingPath;

            wxString destFilePath = parentPath.LastScreen()->GetFileName();

            if( !destFilePath.IsEmpty() )
            {
                SCH_SHEET_LIST schematicSheets = schematic()->Hierarchy();
                SCH_SHEET_LIST loadedSheets( sheet );

                if( schematicSheets.TestForRecursion( loadedSheets, destFilePath ) )
                {
                    status.set_code( ItemStatusCode::ISC_INVALID_DATA );
                    status.set_error_message( "sheet update would create recursive hierarchy" );
                    aItemHandler( status, anyItem );
                    continue;
                }
            }
        }

        status.set_code( ItemStatusCode::ISC_OK );
        google::protobuf::Any newItem;

        if( aCreate )
        {
            SCH_ITEM* createdItem = static_cast<SCH_ITEM*>( item.release() );
            commit->Add( createdItem, targetScreen );

            if( !createdItem )
            {
                e.set_status( ApiStatusCode::AS_BAD_REQUEST );
                e.set_error_message( "could not add the requested item to its parent container" );
                return tl::unexpected( e );
            }

            if( createdItem->Type() == SCH_SYMBOL_T )
            {
                kiapi::schematic::types::SchematicSymbolInstance symbol;

                if( PackSymbol( &symbol, static_cast<SCH_SYMBOL*>( createdItem ), targetPath ) )
                    newItem.PackFrom( symbol );
            }
            else if( createdItem->Type() == SCH_SHEET_T )
            {
                kiapi::schematic::types::SheetSymbol sheet;

                if( PackSheet( &sheet, static_cast<SCH_SHEET*>( createdItem ), targetPath ) )
                    newItem.PackFrom( sheet );
            }
            else
            {
                createdItem->Serialize( newItem );
            }
        }
        else
        {
            commit->Modify( existingItem, targetScreen );
            existingItem->SwapItemData( static_cast<SCH_ITEM*>( item.get() ) );

            if( existingItem->IsConnectable() )
            {
                existingItem->SetConnectivityDirty();
                connectivityChanged = true;
            }

            if( existingItem->Type() == SCH_SYMBOL_T )
            {
                SCH_SHEET_PATH path = existingPath;
                kiapi::schematic::types::SchematicSymbolInstance symbol;

                if( PackSymbol( &symbol, static_cast<SCH_SYMBOL*>( existingItem ), path ) )
                    newItem.PackFrom( symbol );
            }
            else if( existingItem->Type() == SCH_SHEET_T )
            {
                SCH_SHEET_PATH path = existingPath;
                kiapi::schematic::types::SheetSymbol sheet;

                if( PackSheet( &sheet, static_cast<SCH_SHEET*>( existingItem ), path ) )
                    newItem.PackFrom( sheet );
            }
            else
            {
                existingItem->Serialize( newItem );
            }
        }

        aItemHandler( status, newItem );
    }

    if( !m_activeClients.contains( aClientName ) )
    {
        pushCurrentCommit( aClientName, aCreate ? _( "Created items via API" )
                                                : _( "Modified items via API" ) );
    }

    if( m_frame && connectivityChanged )
        m_frame->RecalculateConnections( nullptr, LOCAL_CLEANUP );

    if( m_frame )
    {
        // API edits go straight onto the screen, but the canvas keeps its existing view
        // items, so a symbol created this way draws as a bare body: no pin leads, no
        // reference or value text, until something rebuilds the view. The board handler
        // calls m_frame->Refresh() after its edits; the schematic one never did, which
        // made API-drawn schematics look broken while the saved file was perfectly correct.
        m_frame->HardRedraw();
    }

    return ItemRequestStatus::IRS_OK;
}


void API_HANDLER_SCH::deleteItemsInternal( std::map<KIID, ItemDeletionStatus>& aItemsToDelete,
                                           const std::string& aClientName )
{
    SCH_SHEET_LIST hierarchy = schematic()->Hierarchy();
    COMMIT* commit = getCurrentCommit( aClientName );

    for( auto& [id, status] : aItemsToDelete )
    {
        SCH_SHEET_PATH path;
        SCH_ITEM* item = hierarchy.ResolveItem( id, &path, true );

        if( !item )
            continue;

        if( !s_allowedTypes.contains( item->Type() ) )
        {
            status = ItemDeletionStatus::IDS_IMMUTABLE;
            continue;
        }

        commit->Remove( item, path.LastScreen() );
        status = ItemDeletionStatus::IDS_OK;
    }

    if( !m_activeClients.contains( aClientName ) )
        pushCurrentCommit( aClientName, _( "Deleted items via API" ) );
}


std::optional<EDA_ITEM*> API_HANDLER_SCH::getItemFromDocument( const DocumentSpecifier& aDocument, const KIID& aId )
{
    if( !validateDocument( aDocument ) )
        return std::nullopt;

    SCH_ITEM* item = schematic()->Hierarchy().ResolveItem( aId, nullptr, true );

    if( !item)
        return std::nullopt;

    return item;
}


std::optional<TITLE_BLOCK*> API_HANDLER_SCH::getTitleBlock()
{
    wxCHECK( m_context->GetCurrentSheet(), std::nullopt );
    return &m_context->GetCurrentSheet()->LastScreen()->GetTitleBlock();
}


std::optional<PAGE_INFO> API_HANDLER_SCH::getPageSettings()
{
    wxCHECK( m_context->GetCurrentSheet(), std::nullopt );
    return m_context->GetCurrentSheet()->LastScreen()->GetPageSettings();
}


bool API_HANDLER_SCH::setPageSettings( const PAGE_INFO& aPageInfo )
{
    wxCHECK( m_context->GetCurrentSheet(), false );
    m_context->GetCurrentSheet()->LastScreen()->SetPageSettings( aPageInfo );
    return true;
}


wxString API_HANDLER_SCH::getDrawingSheetFileName()
{
    return BASE_SCREEN::m_DrawingSheetFileName;
}


void API_HANDLER_SCH::setDrawingSheetFileName( const wxString& aFileName )
{
    BASE_SCREEN::m_DrawingSheetFileName = aFileName;
    schematic()->Settings().m_SchDrawingSheetFileName = aFileName;

    if( m_frame )
        m_frame->LoadDrawingSheet();
}


void API_HANDLER_SCH::onModified()
{
    if( m_frame )
    {
        m_frame->Refresh();
        m_frame->OnModify();
    }
}


HANDLER_RESULT<types::RunJobResponse> API_HANDLER_SCH::handleRunSchematicJobExportSvg(
        const HANDLER_CONTEXT<kiapi::schematic::jobs::RunSchematicJobExportSvg>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.job_settings().document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    auto plotJob = std::make_unique<JOB_EXPORT_SCH_PLOT_SVG>();
    plotJob->m_filename = m_context->GetCurrentFileName();

    if( !aCtx.Request.job_settings().output_path().empty() )
        plotJob->SetConfiguredOutputPath( wxString::FromUTF8( aCtx.Request.job_settings().output_path() ) );

    const kiapi::schematic::jobs::SchematicPlotSettings& settings = aCtx.Request.plot_settings();

    plotJob->m_drawingSheet = wxString::FromUTF8( settings.drawing_sheet() );
    plotJob->m_defaultFont = wxString::FromUTF8( settings.default_font() );
    plotJob->m_variant = wxString::FromUTF8( settings.variant() );
    plotJob->m_plotAll = settings.plot_all();
    plotJob->m_plotDrawingSheet = settings.plot_drawing_sheet();
    plotJob->m_show_hop_over = settings.show_hop_over();
    plotJob->m_blackAndWhite = settings.black_and_white();
    plotJob->m_useBackgroundColor = settings.use_background_color();
    plotJob->m_minPenWidth = settings.min_pen_width();
    plotJob->m_theme = wxString::FromUTF8( settings.theme() );

    plotJob->m_plotPages.clear();

    for( const std::string& page : settings.plot_pages() )
        plotJob->m_plotPages.push_back( wxString::FromUTF8( page ) );

    if( aCtx.Request.plot_settings().page_size() != kiapi::schematic::jobs::SchematicJobPageSize::SJPS_UNKNOWN )
    {
        plotJob->m_pageSizeSelect = FromProtoEnum<JOB_PAGE_SIZE>( aCtx.Request.plot_settings().page_size() );
    }

    return ExecuteSchematicJob( m_context->GetKiway(), *plotJob );
}


HANDLER_RESULT<types::RunJobResponse> API_HANDLER_SCH::handleRunSchematicJobExportDxf(
        const HANDLER_CONTEXT<kiapi::schematic::jobs::RunSchematicJobExportDxf>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.job_settings().document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    auto plotJob = std::make_unique<JOB_EXPORT_SCH_PLOT_DXF>();
    plotJob->m_filename = m_context->GetCurrentFileName();

    if( !aCtx.Request.job_settings().output_path().empty() )
        plotJob->SetConfiguredOutputPath( wxString::FromUTF8( aCtx.Request.job_settings().output_path() ) );

    const kiapi::schematic::jobs::SchematicPlotSettings& settings = aCtx.Request.plot_settings();

    plotJob->m_drawingSheet = wxString::FromUTF8( settings.drawing_sheet() );
    plotJob->m_defaultFont = wxString::FromUTF8( settings.default_font() );
    plotJob->m_variant = wxString::FromUTF8( settings.variant() );
    plotJob->m_plotAll = settings.plot_all();
    plotJob->m_plotDrawingSheet = settings.plot_drawing_sheet();
    plotJob->m_show_hop_over = settings.show_hop_over();
    plotJob->m_blackAndWhite = settings.black_and_white();
    plotJob->m_useBackgroundColor = settings.use_background_color();
    plotJob->m_minPenWidth = settings.min_pen_width();
    plotJob->m_theme = wxString::FromUTF8( settings.theme() );

    plotJob->m_plotPages.clear();

    for( const std::string& page : settings.plot_pages() )
        plotJob->m_plotPages.push_back( wxString::FromUTF8( page ) );

    if( aCtx.Request.plot_settings().page_size() != kiapi::schematic::jobs::SchematicJobPageSize::SJPS_UNKNOWN )
    {
        plotJob->m_pageSizeSelect = FromProtoEnum<JOB_PAGE_SIZE>( aCtx.Request.plot_settings().page_size() );
    }

    return ExecuteSchematicJob( m_context->GetKiway(), *plotJob );
}


HANDLER_RESULT<types::RunJobResponse> API_HANDLER_SCH::handleRunSchematicJobExportPdf(
        const HANDLER_CONTEXT<kiapi::schematic::jobs::RunSchematicJobExportPdf>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.job_settings().document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    auto plotJob = std::make_unique<JOB_EXPORT_SCH_PLOT_PDF>( false );
    plotJob->m_filename = m_context->GetCurrentFileName();

    if( !aCtx.Request.job_settings().output_path().empty() )
        plotJob->SetConfiguredOutputPath( wxString::FromUTF8( aCtx.Request.job_settings().output_path() ) );

    const kiapi::schematic::jobs::SchematicPlotSettings& settings = aCtx.Request.plot_settings();

    plotJob->m_drawingSheet = wxString::FromUTF8( settings.drawing_sheet() );
    plotJob->m_defaultFont = wxString::FromUTF8( settings.default_font() );
    plotJob->m_variant = wxString::FromUTF8( settings.variant() );
    plotJob->m_plotAll = settings.plot_all();
    plotJob->m_plotDrawingSheet = settings.plot_drawing_sheet();
    plotJob->m_show_hop_over = settings.show_hop_over();
    plotJob->m_blackAndWhite = settings.black_and_white();
    plotJob->m_useBackgroundColor = settings.use_background_color();
    plotJob->m_minPenWidth = settings.min_pen_width();
    plotJob->m_theme = wxString::FromUTF8( settings.theme() );

    plotJob->m_plotPages.clear();

    for( const std::string& page : settings.plot_pages() )
        plotJob->m_plotPages.push_back( wxString::FromUTF8( page ) );

    if( aCtx.Request.plot_settings().page_size() != kiapi::schematic::jobs::SchematicJobPageSize::SJPS_UNKNOWN )
    {
        plotJob->m_pageSizeSelect = FromProtoEnum<JOB_PAGE_SIZE>( aCtx.Request.plot_settings().page_size() );
    }

    plotJob->m_PDFPropertyPopups = aCtx.Request.property_popups();
    plotJob->m_PDFHierarchicalLinks = aCtx.Request.hierarchical_links();
    plotJob->m_PDFMetadata = aCtx.Request.include_metadata();

    return ExecuteSchematicJob( m_context->GetKiway(), *plotJob );
}


HANDLER_RESULT<types::RunJobResponse> API_HANDLER_SCH::handleRunSchematicJobExportPs(
        const HANDLER_CONTEXT<kiapi::schematic::jobs::RunSchematicJobExportPs>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.job_settings().document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    auto plotJob = std::make_unique<JOB_EXPORT_SCH_PLOT_PS>();
    plotJob->m_filename = m_context->GetCurrentFileName();

    if( !aCtx.Request.job_settings().output_path().empty() )
        plotJob->SetConfiguredOutputPath( wxString::FromUTF8( aCtx.Request.job_settings().output_path() ) );

    const kiapi::schematic::jobs::SchematicPlotSettings& settings = aCtx.Request.plot_settings();

    plotJob->m_drawingSheet = wxString::FromUTF8( settings.drawing_sheet() );
    plotJob->m_defaultFont = wxString::FromUTF8( settings.default_font() );
    plotJob->m_variant = wxString::FromUTF8( settings.variant() );
    plotJob->m_plotAll = settings.plot_all();
    plotJob->m_plotDrawingSheet = settings.plot_drawing_sheet();
    plotJob->m_show_hop_over = settings.show_hop_over();
    plotJob->m_blackAndWhite = settings.black_and_white();
    plotJob->m_useBackgroundColor = settings.use_background_color();
    plotJob->m_minPenWidth = settings.min_pen_width();
    plotJob->m_theme = wxString::FromUTF8( settings.theme() );

    plotJob->m_plotPages.clear();

    for( const std::string& page : settings.plot_pages() )
        plotJob->m_plotPages.push_back( wxString::FromUTF8( page ) );

    if( aCtx.Request.plot_settings().page_size() != kiapi::schematic::jobs::SchematicJobPageSize::SJPS_UNKNOWN )
    {
        plotJob->m_pageSizeSelect = FromProtoEnum<JOB_PAGE_SIZE>( aCtx.Request.plot_settings().page_size() );
    }

    return ExecuteSchematicJob( m_context->GetKiway(), *plotJob );
}


HANDLER_RESULT<types::RunJobResponse> API_HANDLER_SCH::handleRunSchematicJobExportNetlist(
        const HANDLER_CONTEXT<kiapi::schematic::jobs::RunSchematicJobExportNetlist>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.job_settings().document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    if( aCtx.Request.format() == kiapi::schematic::jobs::SchematicNetlistFormat::SNF_UNKNOWN )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "RunSchematicJobExportNetlist requires a valid format" );
        return tl::unexpected( e );
    }

    JOB_EXPORT_SCH_NETLIST netlistJob;
    netlistJob.m_filename = m_context->GetCurrentFileName();

    if( !aCtx.Request.job_settings().output_path().empty() )
        netlistJob.SetConfiguredOutputPath( wxString::FromUTF8( aCtx.Request.job_settings().output_path() ) );

    netlistJob.format = FromProtoEnum<JOB_EXPORT_SCH_NETLIST::FORMAT>( aCtx.Request.format() );

    if( !aCtx.Request.variant_name().empty() )
        netlistJob.m_variantNames.emplace_back( wxString::FromUTF8( aCtx.Request.variant_name() ) );

    return ExecuteSchematicJob( m_context->GetKiway(), netlistJob );
}


HANDLER_RESULT<types::RunJobResponse> API_HANDLER_SCH::handleRunSchematicJobExportBOM(
        const HANDLER_CONTEXT<kiapi::schematic::jobs::RunSchematicJobExportBOM>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.job_settings().document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    JOB_EXPORT_BOM bomJob;
    bomJob.m_filename = m_context->GetCurrentFileName();

    if( !aCtx.Request.job_settings().output_path().empty() )
        bomJob.SetConfiguredOutputPath( wxString::FromUTF8( aCtx.Request.job_settings().output_path() ) );

    bomJob.m_bomFmtPresetName = wxString::FromUTF8( aCtx.Request.format().preset_name() );
    bomJob.m_fieldDelimiter = wxString::FromUTF8( aCtx.Request.format().field_delimiter() );
    bomJob.m_stringDelimiter = wxString::FromUTF8( aCtx.Request.format().string_delimiter() );
    bomJob.m_refDelimiter = wxString::FromUTF8( aCtx.Request.format().ref_delimiter() );
    bomJob.m_refRangeDelimiter = wxString::FromUTF8( aCtx.Request.format().ref_range_delimiter() );
    bomJob.m_keepTabs = aCtx.Request.format().keep_tabs();
    bomJob.m_keepLineBreaks = aCtx.Request.format().keep_line_breaks();
    bomJob.m_includeByteOrderMark = aCtx.Request.format().include_byte_order_mark();

    bomJob.m_bomPresetName = wxString::FromUTF8( aCtx.Request.fields().preset_name() );
    bomJob.m_sortField = wxString::FromUTF8( aCtx.Request.fields().sort_field() );
    bomJob.m_filterString = wxString::FromUTF8( aCtx.Request.fields().filter() );

    if( aCtx.Request.fields().sort_direction() == kiapi::schematic::jobs::BOMSortDirection::BSD_ASCENDING )
    {
        bomJob.m_sortAsc = true;
    }
    else if( aCtx.Request.fields().sort_direction() == kiapi::schematic::jobs::BOMSortDirection::BSD_DESCENDING )
    {
        bomJob.m_sortAsc = false;
    }

    for( const kiapi::schematic::jobs::BOMField& field : aCtx.Request.fields().fields() )
    {
        bomJob.m_fieldsOrdered.emplace_back( wxString::FromUTF8( field.name() ) );
        bomJob.m_fieldsLabels.emplace_back( wxString::FromUTF8( field.label() ) );

        if( field.group_by() )
            bomJob.m_fieldsGroupBy.emplace_back( wxString::FromUTF8( field.name() ) );
    }

    bomJob.m_excludeDNP = aCtx.Request.exclude_dnp();
    bomJob.m_groupSymbols = aCtx.Request.group_symbols();

    if( !aCtx.Request.variant_name().empty() )
        bomJob.m_variantNames.emplace_back( wxString::FromUTF8( aCtx.Request.variant_name() ) );

    return ExecuteSchematicJob( m_context->GetKiway(), bomJob );
}


void API_HANDLER_SCH::packSheetInstance( kiapi::schematic::types::SheetInstance* aInstance, SCH_SHEET_PATH& aPath,
                                          SCH_SHEET* aSheet )
{
    aPath.push_back( aSheet );

    PackSheetPath( *aInstance->mutable_path(), aPath.Path() );

    wxString sheetName = aSheet->GetShownName( false );

    if( sheetName.IsEmpty() && aSheet->GetScreen() )
    {
        wxFileName fn( aSheet->GetScreen()->GetFileName() );
        sheetName = fn.GetName();
    }

    aInstance->set_name( sheetName.ToUTF8() );
    aInstance->set_filename( aSheet->GetFileName().ToUTF8() );
    aInstance->set_page_number( aPath.GetPageNumber().ToUTF8() );

    if( aSheet->GetScreen() )
    {
        std::vector<SCH_ITEM*> childSheets;
        aSheet->GetScreen()->GetSheets( &childSheets );

        std::ranges::sort( childSheets,
                           [&]( SCH_ITEM* a, SCH_ITEM* b )
                           {
                               SCH_SHEET_PATH pathA = aPath;
                               pathA.push_back( static_cast<SCH_SHEET*>( a ) );

                               SCH_SHEET_PATH pathB = aPath;
                               pathB.push_back( static_cast<SCH_SHEET*>( b ) );

                               return pathA.ComparePageNum( pathB ) < 0;
                           } );

        for( SCH_ITEM* childItem : childSheets )
        {
            SCH_SHEET* childSheet = static_cast<SCH_SHEET*>( childItem );
            kiapi::schematic::types::SheetInstance* childInstance = aInstance->add_children();
            packSheetInstance( childInstance, aPath, childSheet );
        }
    }

    aPath.pop_back();
}


HANDLER_RESULT<kiapi::schematic::types::SchematicHierarchyResponse> API_HANDLER_SCH::handleGetSchematicHierarchy(
        const HANDLER_CONTEXT<kiapi::schematic::types::GetSchematicHierarchy>& aCtx )
{
    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    kiapi::schematic::types::SchematicHierarchyResponse response;
    response.mutable_document()->CopyFrom( aCtx.Request.document() );

    if( !schematic()->HasHierarchy() )
        schematic()->RefreshHierarchy();

    SCH_SHEET_PATH path;
    std::vector<SCH_SHEET*> topLevelSheets = schematic()->GetTopLevelSheets();

    std::ranges::sort( topLevelSheets,
               [&]( SCH_SHEET* a, SCH_SHEET* b )
               {
                   SCH_SHEET_PATH pathA;
                   pathA.push_back( a );

                   SCH_SHEET_PATH pathB;
                   pathB.push_back( b );

                   return pathA.ComparePageNum( pathB ) < 0;
               } );

    for( SCH_SHEET* topSheet : topLevelSheets )
    {
        kiapi::schematic::types::SheetInstance* instance = response.add_top_level_sheets();
        packSheetInstance( instance, path, topSheet );
    }

    return response;
}


HANDLER_RESULT<kiapi::schematic::types::SchematicNetlistResponse>
API_HANDLER_SCH::handleGetSchematicNetlist( const HANDLER_CONTEXT<kiapi::schematic::types::GetSchematicNetlist>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.document() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    std::vector<KICAD_T> types = parseRequestedItemTypes( aCtx.Request.types() );
    const bool filterByType = aCtx.Request.types_size() > 0;

    if( filterByType && types.empty() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "none of the requested types are valid for a Schematic object" );
        return tl::unexpected( e );
    }

    std::set<KICAD_T> typeFilter( types.begin(), types.end() );

    CONNECTION_GRAPH* connectionGraph = schematic()->ConnectionGraph();

    if( !connectionGraph )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "schematic has no connection graph" );
        return tl::unexpected( e );
    }

    kiapi::schematic::types::SchematicNetlistResponse response;
    response.mutable_document()->CopyFrom( aCtx.Request.document() );

    for( const auto& [key, subgraphList] : connectionGraph->GetNetMap() )
    {
        if( subgraphList.empty() )
            continue;

        CONNECTION_SUBGRAPH* firstSubgraph = subgraphList[0];

        if( firstSubgraph->GetDriverConnection() && firstSubgraph->GetDriverConnection()->IsBus() )
            continue;

        if( firstSubgraph->GetDriverPriority() < CONNECTION_SUBGRAPH::PRIORITY::PIN )
            continue;

        kiapi::schematic::types::SchematicNet* net = response.add_nets();
        net->set_name( key.Name.ToUTF8() );

        for( CONNECTION_SUBGRAPH* subGraph : subgraphList )
        {
            kiapi::schematic::types::SchematicNetSheetContents* sheetContents = net->add_sheets();
            PackSheetPath( *sheetContents->mutable_path(), subGraph->GetSheet().Path() );

            for( SCH_ITEM* item : subGraph->GetItems() )
            {
                if( filterByType && !typeFilter.contains( item->Type() ) )
                    continue;

                sheetContents->add_items()->set_value( item->m_Uuid.AsStdString() );
            }
        }
    }

    return response;
}


HANDLER_RESULT<kiapi::schematic::types::PlaceSymbolResponse> API_HANDLER_SCH::handlePlaceSymbol(
        const HANDLER_CONTEXT<kiapi::schematic::types::PlaceSymbol>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.schematic() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    LIB_ID libId( wxString::FromUTF8( aCtx.Request.lib_id().library_nickname() ),
                  wxString::FromUTF8( aCtx.Request.lib_id().entry_name() ) );

    if( !libId.IsValid() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "a library nickname and entry name are both required" );
        return tl::unexpected( e );
    }

    // Resolve through the project's adapter, which is what the interactive tool uses. A
    // LIB_ID that is not in any configured library is a request error, not an empty result.
    SYMBOL_LIBRARY_ADAPTER* adapter = PROJECT_SCH::SymbolLibAdapter( &schematic()->Project() );

    // See handleSearchSymbols: without this, a place request that arrives before the
    // startup preload finishes fails with "no symbol X in the configured libraries",
    // which reads like a bad LIB_ID rather than a race.
    if( adapter )
        adapter->BlockUntilLoaded();

    LIB_SYMBOL* libSymbol = adapter ? adapter->LoadSymbol( libId ) : nullptr;

    if( !libSymbol )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "no symbol {} in the configured libraries",
                                          libId.Format().wx_str().ToStdString() ) );
        return tl::unexpected( e );
    }

    SCH_SHEET_LIST hierarchy = schematic()->Hierarchy();
    SCH_SHEET_PATH targetPath = m_context->GetCurrentSheet().value_or( *hierarchy.begin() );

    if( aCtx.Request.schematic().has_sheet_path() )
    {
        KIID_PATH kp = UnpackSheetPath( aCtx.Request.schematic().sheet_path() );

        if( std::optional<SCH_SHEET_PATH> path = hierarchy.GetSheetPathByKIIDPath( kp ) )
            targetPath = *path;
    }

    SCH_SCREEN* screen = targetPath.LastScreen();

    if( !screen )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "the target sheet has no screen" );
        return tl::unexpected( e );
    }

    int unit = std::max( 1, (int) aCtx.Request.unit() );
    int bodyStyle = std::max( 1, (int) aCtx.Request.body_style() );
    VECTOR2I pos = UnpackVector2( aCtx.Request.position(), schIUScale );

    // The constructor the interactive placement tool uses (sch_drawing_tools.cpp). Going
    // through it -- rather than building a SCH_SYMBOL and deserializing onto it -- is what
    // gets the unit, the transform and the SCH_SYMBOL_INSTANCE right, and keeps the
    // LIB_SYMBOL's own draw items in symbol space where they belong.
    SCH_SYMBOL* symbol = new SCH_SYMBOL( *libSymbol, libId, &targetPath, unit, bodyStyle,
                                         pos, schematic() );

    if( !aCtx.Request.reference().empty() )
    {
        symbol->SetRef( &targetPath, wxString::FromUTF8( aCtx.Request.reference() ) );
        symbol->GetField( FIELD_T::REFERENCE )
                ->SetText( wxString::FromUTF8( aCtx.Request.reference() ) );
    }

    if( !aCtx.Request.value().empty() )
        symbol->SetValueFieldText( wxString::FromUTF8( aCtx.Request.value() ) );

    if( aCtx.Request.has_transform() )
    {
        const kiapi::schematic::types::SchematicSymbolTransform& xf = aCtx.Request.transform();

        // SSO_UNKNOWN is the proto3 default, so an unset transform must not be read as a
        // rotation request; leave the symbol at SSO_0.
        if( xf.orientation() != kiapi::schematic::types::SSO_UNKNOWN )
        {
            symbol->SetOrientationProp(
                    FromProtoEnum<SYMBOL_ORIENTATION_PROP>( xf.orientation() ) );
        }

        if( xf.mirror_x() )
            symbol->SetMirrorX( true );

        if( xf.mirror_y() )
            symbol->SetMirrorY( true );
    }

    SCH_COMMIT commit( m_frame ? static_cast<TOOL_MANAGER*>( m_frame->GetToolManager() )
                               : m_context->GetToolManager() );
    commit.Add( symbol, screen );
    commit.Push( _( "Place symbol via API" ) );

    if( m_frame )
        m_frame->HardRedraw();

    kiapi::schematic::types::PlaceSymbolResponse response;

    if( !PackSymbol( response.mutable_symbol(), symbol, targetPath ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNKNOWN );
        e.set_error_message( "symbol was placed but could not be serialised for the reply" );
        return tl::unexpected( e );
    }

    return response;
}


/// Default cap on SearchSymbols results. The stock libraries hold tens of thousands of
/// symbols; an unbounded query would return a reply no caller wants.
static constexpr int MAX_SYMBOL_SEARCH_RESULTS = 250;


HANDLER_RESULT<kiapi::schematic::types::SearchSymbolsResponse> API_HANDLER_SCH::handleSearchSymbols(
        const HANDLER_CONTEXT<kiapi::schematic::types::SearchSymbols>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.schematic() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    SYMBOL_LIBRARY_ADAPTER* adapter = PROJECT_SCH::SymbolLibAdapter( &schematic()->Project() );

    if( !adapter )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNKNOWN );
        e.set_error_message( "no symbol library adapter is available" );
        return tl::unexpected( e );
    }

    // eeschema kicks off PreloadLibraries() asynchronously at startup, so a client that
    // connects as soon as the API answers can get here first. GetSymbols() then returns an
    // empty vector rather than an error, and the search silently reports zero results for
    // a perfectly good library. cvpcb blocks the same way before it enumerates.
    adapter->BlockUntilLoaded();

    std::vector<wxString> nicknames;

    if( aCtx.Request.libraries().empty() )
    {
        nicknames = adapter->GetLibraryNames();
    }
    else
    {
        for( const std::string& nickname : aCtx.Request.libraries() )
        {
            wxString nick = wxString::FromUTF8( nickname );

            if( !adapter->GetRow( nick ) )
            {
                ApiResponseStatus e;
                e.set_status( ApiStatusCode::AS_BAD_REQUEST );
                e.set_error_message( fmt::format( "no such symbol library: {}", nickname ) );
                return tl::unexpected( e );
            }

            nicknames.push_back( nick );
        }
    }

    if( nicknames.empty() )
    {
        // Distinct from "nothing matched": with no libraries configured there is nothing
        // to match against, and the caller's real problem is configuration.
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "no symbol libraries are configured; check the global and "
                             "project symbol library tables" );
        return tl::unexpected( e );
    }

    std::vector<wxString> terms;
    wxString              query = wxString::FromUTF8( aCtx.Request.query() ).Lower();

    if( aCtx.Request.match_all_terms() )
    {
        wxStringTokenizer tokenizer( query );

        while( tokenizer.HasMoreTokens() )
            terms.push_back( tokenizer.GetNextToken() );
    }
    else if( !query.IsEmpty() )
    {
        terms.push_back( query );
    }

    int limit = aCtx.Request.max_results() > 0 ? (int) aCtx.Request.max_results()
                                               : MAX_SYMBOL_SEARCH_RESULTS;

    SYMBOL_LIBRARY_ADAPTER::SYMBOL_TYPE wanted =
            aCtx.Request.power_only() ? SYMBOL_LIBRARY_ADAPTER::SYMBOL_TYPE::POWER_ONLY
                                      : SYMBOL_LIBRARY_ADAPTER::SYMBOL_TYPE::ALL_SYMBOLS;

    kiapi::schematic::types::SearchSymbolsResponse response;

    // Matches are ranked before the limit is applied, because an exact name match must
    // beat a keyword match. Searching power symbols for "GND" otherwise returns Earth
    // first -- its keywords are "global ground gnd" -- and a caller taking results[0]
    // silently wires the board to a net called Earth. Observed, not hypothetical.
    struct HIT
    {
        int         rank;       // lower is better
        wxString    nickname;
        LIB_SYMBOL* symbol;
    };

    std::vector<HIT> hits;
    int              indexed = 0;

    auto rankOf =
            [&]( const wxString& aName ) -> int
            {
                if( terms.empty() )
                    return 3;

                wxString name = aName.Lower();

                if( name == query )
                    return 0;
                if( name.StartsWith( query ) )
                    return 1;
                if( name.Contains( query ) )
                    return 2;

                return 3;   // matched only on library, description or keywords
            };

    for( const wxString& nickname : nicknames )
    {
        std::vector<LIB_SYMBOL*> symbols;

        try
        {
            symbols = adapter->GetSymbols( nickname, wanted );
        }
        catch( const std::exception& exc )
        {
            // One unreadable library must not abort the whole search, but it must not
            // vanish either -- a short list would be indistinguishable from a correct one.
            response.add_errors( fmt::format( "{}: {}", nickname.ToStdString(), exc.what() ) );
            continue;
        }

        for( LIB_SYMBOL* symbol : symbols )
        {
            if( !symbol )
                continue;

            indexed++;

            // Matched per field rather than against one concatenated string: joining them
            // lets a query straddle a boundary, which is how "0603 resistor" once matched
            // a footprint on the seam between its name and its library nickname.
            const wxString fields[] = { symbol->GetName().Lower(),
                                        nickname.Lower(),
                                        symbol->GetDescription().Lower(),
                                        symbol->GetKeyWords().Lower() };

            bool hit = std::ranges::all_of( terms,
                                            [&]( const wxString& term )
                                            {
                                                return std::ranges::any_of(
                                                        fields,
                                                        [&]( const wxString& field )
                                                        {
                                                            return field.Contains( term );
                                                        } );
                                            } );

            if( hit )
                hits.push_back( { rankOf( symbol->GetName() ), nickname, symbol } );
        }
    }

    std::ranges::stable_sort( hits,
                              []( const HIT& a, const HIT& b )
                              {
                                  return a.rank < b.rank;
                              } );

    if( (int) hits.size() > limit )
        response.set_truncated( true );

    for( const HIT& h : hits | std::views::take( limit ) )
    {
        kiapi::schematic::types::SymbolSearchResult* out = response.add_results();
        out->mutable_id()->set_library_nickname( h.nickname.ToStdString() );
        out->mutable_id()->set_entry_name( h.symbol->GetName().ToStdString() );
        out->set_description( h.symbol->GetDescription().ToStdString() );
        out->set_keywords( h.symbol->GetKeyWords().ToStdString() );
        out->set_pin_count( (uint32_t) h.symbol->GetPins().size() );
        out->set_unit_count( (uint32_t) h.symbol->GetUnitCount() );
        out->set_is_power( h.symbol->IsPower() );
    }

    response.set_total_indexed( (uint32_t) indexed );
    return response;
}


static EDA_UNITS ercUnitsFromProto( kiapi::common::types::Units aUnits )
{
    switch( aUnits )
    {
    case kiapi::common::types::Units::U_INCH:      return EDA_UNITS::INCH;
    case kiapi::common::types::Units::U_MILS:        return EDA_UNITS::MILS;
    case kiapi::common::types::Units::U_MM: return EDA_UNITS::MM;
    default:                                         return EDA_UNITS::MM;
    }
}


static void packErcViolation( const RC_JSON::VIOLATION& aSrc, EDA_UNITS aUnits,
                              const wxString& aSheetPath, const wxString& aSheetUuidPath,
                              kiapi::schematic::types::ErcViolation* aOut )
{
    aOut->set_type( aSrc.type.ToStdString() );
    aOut->set_description( aSrc.description.ToStdString() );
    aOut->set_excluded( aSrc.excluded );
    aOut->set_sheet_path( aSheetPath.ToStdString() );
    aOut->set_sheet_uuid_path( aSheetUuidPath.ToStdString() );

    if( aSrc.excluded )
        aOut->set_exclusion_comment( aSrc.comment.ToStdString() );

    // RC_JSON carries severity as a string because that is what the JSON schema
    // publishes; map it back to the enum rather than leaking strings into the API.
    const wxString& sev = aSrc.severity;

    if( sev == wxS( "error" ) )
        aOut->set_severity( kiapi::common::types::RuleSeverity::RS_ERROR );
    else if( sev == wxS( "warning" ) )
        aOut->set_severity( kiapi::common::types::RuleSeverity::RS_WARNING );
    else if( sev == wxS( "exclusion" ) )
        aOut->set_severity( kiapi::common::types::RuleSeverity::RS_EXCLUSION );
    else if( sev == wxS( "info" ) )
        aOut->set_severity( kiapi::common::types::RuleSeverity::RS_INFO );
    else if( sev == wxS( "ignore" ) )
        aOut->set_severity( kiapi::common::types::RuleSeverity::RS_IGNORE );
    else
        aOut->set_severity( kiapi::common::types::RuleSeverity::RS_UNKNOWN );

    for( const RC_JSON::AFFECTED_ITEM& item : aSrc.items )
    {
        kiapi::schematic::types::ErcAffectedItem* outItem = aOut->add_items();
        outItem->mutable_id()->set_value( item.uuid.ToStdString() );
        outItem->set_description( item.description.ToStdString() );

        // RC_JSON carries coordinates as doubles already scaled to the report units;
        // Vector2 is int64 nanometres.
        //
        // FromUserUnit returns *internal* units, and a schematic IU is 100 nm
        // (SCH_IU_PER_MM = 1e4), not 1 nm. The board handler gets away with storing the
        // result straight into x_nm only because a pcbnew IU happens to be a nanometre.
        // Copying that here reported every ERC coordinate 100x too small -- 0.01 mm where
        // `kicad-cli sch erc` said 1.0428 mm. Go IU -> mm -> nm explicitly.
        auto toNanometres =
                [&]( double aValue ) -> int64_t
                {
                    double iu = EDA_UNIT_UTILS::UI::FromUserUnit( schIUScale, aUnits, aValue );
                    return KiROUND( iu / SCH_IU_PER_MM * 1e6 );
                };

        outItem->mutable_position()->set_x_nm( toNanometres( item.pos.x ) );
        outItem->mutable_position()->set_y_nm( toNanometres( item.pos.y ) );
    }
}


HANDLER_RESULT<kiapi::schematic::types::RunErcResponse> API_HANDLER_SCH::handleRunErc(
        const HANDLER_CONTEXT<kiapi::schematic::types::RunErc>& aCtx )
{
    if( std::optional<ApiResponseStatus> busy = checkForBusy() )
        return tl::unexpected( *busy );

    HANDLER_RESULT<bool> documentValidation = validateDocument( aCtx.Request.schematic() );

    if( !documentValidation )
        return tl::unexpected( documentValidation.error() );

    SCHEMATIC* sch = schematic();

    if( !sch )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "no schematic is open" );
        return tl::unexpected( e );
    }

    const EDA_UNITS units = ercUnitsFromProto( aCtx.Request.units() );

    // Severity mask. An empty request means the common case -- errors and warnings,
    // exclusions omitted -- rather than "report nothing".
    int severities = 0;

    if( aCtx.Request.severities().empty() )
    {
        severities = RPT_SEVERITY_ERROR | RPT_SEVERITY_WARNING;
    }
    else
    {
        for( int severity : aCtx.Request.severities() )
        {
            switch( static_cast<kiapi::common::types::RuleSeverity>( severity ) )
            {
            case kiapi::common::types::RuleSeverity::RS_ERROR:
                severities |= RPT_SEVERITY_ERROR;
                break;
            case kiapi::common::types::RuleSeverity::RS_WARNING:
                severities |= RPT_SEVERITY_WARNING;
                break;
            case kiapi::common::types::RuleSeverity::RS_EXCLUSION:
                severities |= RPT_SEVERITY_EXCLUSION;
                break;
            case kiapi::common::types::RuleSeverity::RS_INFO:
                severities |= RPT_SEVERITY_INFO;
                break;
            default:
                break;
            }
        }
    }

    std::shared_ptr<SHEETLIST_ERC_ITEMS_PROVIDER> markersProvider =
            std::make_shared<SHEETLIST_ERC_ITEMS_PROVIDER>( sch );

    // ERC resolves symbols against their libraries, so they have to be loaded. The
    // startup preload is asynchronous, and a client that connects promptly gets here
    // first; without this ERC quietly reports library problems that do not exist.
    if( SYMBOL_LIBRARY_ADAPTER* adapter = PROJECT_SCH::SymbolLibAdapter( &sch->Project() ) )
    {
        adapter->AsyncLoad();
        adapter->BlockUntilLoaded();
    }

    try
    {
        ERC_TESTER tester( sch );

        // The drawing-sheet proxy is only used for checks against the sheet border, and
        // the CVPCB kiface only for footprint-link testing. Both are optional; passing
        // nullptr skips those checks rather than failing.
        tester.RunTests( nullptr, nullptr, m_frame ? m_frame->Kiway().KiFACE( KIWAY::FACE_CVPCB )
                                                   : nullptr,
                         &sch->Project(), nullptr );
    }
    catch( const std::exception& exc )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNKNOWN );
        e.set_error_message( fmt::format( "ERC failed: {}", exc.what() ) );
        return tl::unexpected( e );
    }

    markersProvider->SetSeverities( severities );

    ERC_REPORT           reporter( sch, units, markersProvider );
    RC_JSON::ERC_REPORT  report = reporter.BuildReport();

    kiapi::schematic::types::RunErcResponse response;

    // ERC groups findings per sheet; flatten them but keep the sheet on each
    // violation, because on a hierarchical design the same check firing on two
    // sheets is two different problems.
    for( const RC_JSON::ERC_SHEET& sheet : report.sheets )
    {
        for( const RC_JSON::VIOLATION& v : sheet.violations )
            packErcViolation( v, units, sheet.path, sheet.uuid_path, response.add_violations() );
    }

    for( const RC_JSON::IGNORED_CHECK& check : report.ignored_checks )
    {
        kiapi::schematic::types::ErcIgnoredCheck* out = response.add_ignored_checks();
        out->set_key( check.key.ToStdString() );
        out->set_description( check.description.ToStdString() );
    }

    response.set_units( aCtx.Request.units() == kiapi::common::types::Units::U_UNKNOWN
                                ? kiapi::common::types::Units::U_MM
                                : aCtx.Request.units() );

    if( severities & RPT_SEVERITY_ERROR )
        response.add_included_severities( kiapi::common::types::RuleSeverity::RS_ERROR );

    if( severities & RPT_SEVERITY_WARNING )
        response.add_included_severities( kiapi::common::types::RuleSeverity::RS_WARNING );

    if( severities & RPT_SEVERITY_EXCLUSION )
        response.add_included_severities( kiapi::common::types::RuleSeverity::RS_EXCLUSION );

    if( severities & RPT_SEVERITY_INFO )
        response.add_included_severities( kiapi::common::types::RuleSeverity::RS_INFO );

    return response;
}
