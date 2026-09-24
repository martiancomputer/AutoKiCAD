/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright (C) 2023 Jon Evans <jon@craftyjon.com>
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

#include <ranges>
#include <tuple>

#include <api/api_handler_common.h>
#include <build_version.h>
#include <eda_shape.h>
#include <eda_text.h>
#include <gestfich.h>
#include <geometry/shape_compound.h>
#include <google/protobuf/empty.pb.h>
#include <paths.h>
#include <pgm_base.h>
#include <api/api_plugin.h>
#include <stdexcept>
#include <transline_calculations/coax.h>
#include <transline_calculations/coplanar.h>
#include <transline_calculations/coupled_microstrip.h>
#include <transline_calculations/coupled_stripline.h>
#include <transline_calculations/microstrip.h>
#include <transline_calculations/rectwaveguide.h>
#include <transline_calculations/stripline.h>
#include <transline_calculations/twistedpair.h>
#include <api/api_utils.h>
#include <libraries/library_manager.h>
#include <libraries/library_table.h>
#include <project/net_settings.h>
#include <project/project_file.h>
#include <settings/settings_manager.h>
#include <wx/string.h>

using namespace kiapi::common::commands;
using namespace kiapi::common::types;
using google::protobuf::Empty;


API_HANDLER_COMMON::API_HANDLER_COMMON() :
        API_HANDLER()
{
    registerHandler<commands::GetVersion, GetVersionResponse>( &API_HANDLER_COMMON::handleGetVersion );
    registerHandler<GetKiCadBinaryPath, PathResponse>(
            &API_HANDLER_COMMON::handleGetKiCadBinaryPath );
    registerHandler<GetNetClasses, NetClassesResponse>( &API_HANDLER_COMMON::handleGetNetClasses );
    registerHandler<SetNetClasses, Empty>( &API_HANDLER_COMMON::handleSetNetClasses );
    registerHandler<Ping, Empty>( &API_HANDLER_COMMON::handlePing );
    registerHandler<RunTransmissionLineCalculation, RunTransmissionLineCalculationResponse>(
            &API_HANDLER_COMMON::handleRunTransmissionLineCalculation );
    registerHandler<GetTextExtents, types::Box2>( &API_HANDLER_COMMON::handleGetTextExtents );
    registerHandler<GetTextAsShapes, GetTextAsShapesResponse>(
            &API_HANDLER_COMMON::handleGetTextAsShapes );
    registerHandler<ExpandTextVariables, ExpandTextVariablesResponse>(
            &API_HANDLER_COMMON::handleExpandTextVariables );
    registerHandler<GetPluginSettingsPath, StringResponse>(
            &API_HANDLER_COMMON::handleGetPluginSettingsPath );
    registerHandler<GetTextVariables, project::TextVariables>(
            &API_HANDLER_COMMON::handleGetTextVariables );
    registerHandler<SetTextVariables, Empty>(
            &API_HANDLER_COMMON::handleSetTextVariables );
    registerHandler<ListLibraries, ListLibrariesResponse>(
            &API_HANDLER_COMMON::handleListLibraries );
    registerHandler<OpenDocument, OpenDocumentResponse>(
            &API_HANDLER_COMMON::handleOpenDocument );
    registerHandler<CloseDocument, Empty>(
            &API_HANDLER_COMMON::handleCloseDocument );
    registerHandler<CloseAllDocuments, Empty>(
            &API_HANDLER_COMMON::handleCloseAllDocuments );

}


HANDLER_RESULT<GetVersionResponse> API_HANDLER_COMMON::handleGetVersion(
        const HANDLER_CONTEXT<commands::GetVersion>& )
{
    GetVersionResponse reply;

    reply.mutable_version()->set_full_version( GetBuildVersion().ToStdString() );

    std::tuple<int, int, int> version = GetMajorMinorPatchTuple();
    reply.mutable_version()->set_major( std::get<0>( version ) );
    reply.mutable_version()->set_minor( std::get<1>( version ) );
    reply.mutable_version()->set_patch( std::get<2>( version ) );

    return reply;
}


HANDLER_RESULT<PathResponse> API_HANDLER_COMMON::handleGetKiCadBinaryPath(
        const HANDLER_CONTEXT<GetKiCadBinaryPath>& aCtx )
{
    wxFileName fn( wxEmptyString, wxString::FromUTF8( aCtx.Request.binary_name() ) );
#ifdef _WIN32
    fn.SetExt( wxT( "exe" ) );
#endif

    wxString path = FindKicadFile( fn.GetFullName() );
    PathResponse reply;
    reply.set_path( path.ToUTF8() );
    return reply;
}


HANDLER_RESULT<NetClassesResponse> API_HANDLER_COMMON::handleGetNetClasses(
        const HANDLER_CONTEXT<GetNetClasses>& aCtx )
{
    NetClassesResponse reply;

    std::shared_ptr<NET_SETTINGS>& netSettings =
            Pgm().GetSettingsManager().Prj().GetProjectFile().m_NetSettings;

    google::protobuf::Any any;

    netSettings->GetDefaultNetclass()->Serialize( any );
    any.UnpackTo( reply.add_net_classes() );

    for( const auto& netClass : netSettings->GetNetclasses() | std::views::values )
    {
        netClass->Serialize( any );
        any.UnpackTo( reply.add_net_classes() );
    }

    return reply;
}


HANDLER_RESULT<Empty> API_HANDLER_COMMON::handleSetNetClasses(
        const HANDLER_CONTEXT<SetNetClasses>& aCtx )
{
    std::shared_ptr<NET_SETTINGS>& netSettings =
            Pgm().GetSettingsManager().Prj().GetProjectFile().m_NetSettings;

    if( aCtx.Request.merge_mode() == MapMergeMode::MMM_REPLACE )
        netSettings->ClearNetclasses();

    auto netClasses = netSettings->GetNetclasses();
    google::protobuf::Any any;

    for( const auto& ncProto : aCtx.Request.net_classes() )
    {
        any.PackFrom( ncProto );
        wxString name = wxString::FromUTF8( ncProto.name() );

        if( name == wxT( "Default" ) )
        {
            netSettings->GetDefaultNetclass()->Deserialize( any );
        }
        else
        {
            if( !netClasses.contains( name ) )
                netClasses.insert( { name, std::make_shared<NETCLASS>( name, false ) } );

            netClasses[name]->Deserialize( any );
        }
    }

    netSettings->SetNetclasses( netClasses );

    return Empty();
}


HANDLER_RESULT<Empty> API_HANDLER_COMMON::handlePing( const HANDLER_CONTEXT<Ping>& aCtx )
{
    return Empty();
}


HANDLER_RESULT<types::Box2> API_HANDLER_COMMON::handleGetTextExtents(
        const HANDLER_CONTEXT<GetTextExtents>& aCtx )
{
    EDA_TEXT text( pcbIUScale );
    google::protobuf::Any any;
    any.PackFrom( aCtx.Request.text() );

    if( !text.Deserialize( any ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "Could not decode text in GetTextExtents message" );
        return tl::unexpected( e );
    }

    types::Box2 response;

    BOX2I bbox = text.GetTextBox( nullptr );
    EDA_ANGLE angle = text.GetTextAngle();

    if( !angle.IsZero() )
        bbox = bbox.GetBoundingBoxRotated( text.GetTextPos(), text.GetTextAngle() );

    response.mutable_position()->set_x_nm( bbox.GetPosition().x );
    response.mutable_position()->set_y_nm( bbox.GetPosition().y );
    response.mutable_size()->set_x_nm( bbox.GetSize().x );
    response.mutable_size()->set_y_nm( bbox.GetSize().y );

    return response;
}


HANDLER_RESULT<GetTextAsShapesResponse> API_HANDLER_COMMON::handleGetTextAsShapes(
        const HANDLER_CONTEXT<GetTextAsShapes>& aCtx )
{
    GetTextAsShapesResponse reply;

    for( const TextOrTextBox& textMsg : aCtx.Request.text() )
    {
        Text dummyText;
        const Text* textPtr = &textMsg.text();

        if( textMsg.has_textbox() )
        {
            dummyText.set_text( textMsg.textbox().text() );
            dummyText.mutable_attributes()->CopyFrom( textMsg.textbox().attributes() );
            textPtr = &dummyText;
        }

        EDA_TEXT text( pcbIUScale );
        google::protobuf::Any any;
        any.PackFrom( *textPtr );

        if( !text.Deserialize( any ) )
        {
            ApiResponseStatus e;
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message( "Could not decode text in GetTextAsShapes message" );
            return tl::unexpected( e );
        }

        std::shared_ptr<SHAPE_COMPOUND> shapes = text.GetEffectiveTextShape( false );

        TextWithShapes* entry = reply.add_text_with_shapes();
        entry->mutable_text()->CopyFrom( textMsg );

        for( SHAPE* subshape : shapes->Shapes() )
        {
            EDA_SHAPE proxy( *subshape );
            proxy.Serialize( any );
            GraphicShape* shapeMsg = entry->mutable_shapes()->add_shapes();
            any.UnpackTo( shapeMsg );
        }

        if( textMsg.has_textbox() )
        {
            GraphicShape* border = entry->mutable_shapes()->add_shapes();
            int width = textMsg.textbox().attributes().stroke_width().value_nm();
            border->mutable_attributes()->mutable_stroke()->mutable_width()->set_value_nm( width );
            VECTOR2I tl = UnpackVector2( textMsg.textbox().top_left() );
            VECTOR2I br = UnpackVector2( textMsg.textbox().bottom_right() );

            // top
            PackVector2( *border->mutable_segment()->mutable_start(), tl );
            PackVector2( *border->mutable_segment()->mutable_end(), VECTOR2I( br.x, tl.y ) );

            // right
            border = entry->mutable_shapes()->add_shapes();
            border->mutable_attributes()->mutable_stroke()->mutable_width()->set_value_nm( width );
            PackVector2( *border->mutable_segment()->mutable_start(), VECTOR2I( br.x, tl.y ) );
            PackVector2( *border->mutable_segment()->mutable_end(), br );

            // bottom
            border = entry->mutable_shapes()->add_shapes();
            border->mutable_attributes()->mutable_stroke()->mutable_width()->set_value_nm( width );
            PackVector2( *border->mutable_segment()->mutable_start(), br );
            PackVector2( *border->mutable_segment()->mutable_end(), VECTOR2I( tl.x, br.y ) );

            // left
            border = entry->mutable_shapes()->add_shapes();
            border->mutable_attributes()->mutable_stroke()->mutable_width()->set_value_nm( width );
            PackVector2( *border->mutable_segment()->mutable_start(), VECTOR2I( tl.x, br.y ) );
            PackVector2( *border->mutable_segment()->mutable_end(), tl );
        }
    }

    return reply;
}


HANDLER_RESULT<ExpandTextVariablesResponse> API_HANDLER_COMMON::handleExpandTextVariables(
        const HANDLER_CONTEXT<ExpandTextVariables>& aCtx )
{
    if( !aCtx.Request.has_document() || aCtx.Request.document().type() != DOCTYPE_PROJECT )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        // No error message, this is a flag that the server should try a different handler
        return tl::unexpected( e );
    }

    ExpandTextVariablesResponse reply;
    PROJECT& project = Pgm().GetSettingsManager().Prj();

    for( const std::string& textMsg : aCtx.Request.text() )
    {
        wxString result = ExpandTextVars( wxString::FromUTF8( textMsg ), &project );
        reply.add_text( result.ToUTF8() );
    }

    return reply;
}


HANDLER_RESULT<StringResponse> API_HANDLER_COMMON::handleGetPluginSettingsPath(
        const HANDLER_CONTEXT<GetPluginSettingsPath>& aCtx )
{
    wxString identifier = wxString::FromUTF8( aCtx.Request.identifier() );

    if( identifier.IsEmpty() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "plugin identifier is missing" );
        return tl::unexpected( e );
    }

    if( !API_PLUGIN::IsValidIdentifier( identifier ) )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "plugin identifier is invalid" );
        return tl::unexpected( e );
    }

    wxFileName path( PATHS::GetUserSettingsPath(), wxEmptyString );
    path.AppendDir( "plugins" );

    // Create the base plugins path if needed, but leave the specific plugin to create its own path
    PATHS::EnsurePathExists( path.GetPath() );

    path.AppendDir( identifier );

    StringResponse reply;
    reply.set_response( path.GetPath() );
    return reply;
}


HANDLER_RESULT<project::TextVariables> API_HANDLER_COMMON::handleGetTextVariables(
        const HANDLER_CONTEXT<GetTextVariables>& aCtx )
{
    if( !aCtx.Request.has_document() || aCtx.Request.document().type() != DOCTYPE_PROJECT )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        // No error message, this is a flag that the server should try a different handler
        return tl::unexpected( e );
    }

    const PROJECT& project = Pgm().GetSettingsManager().Prj();

    if( project.IsNullProject() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_NOT_READY );
        e.set_error_message( "no valid project is loaded, cannot get text variables" );
        return tl::unexpected( e );
    }

    const std::map<wxString, wxString>& vars = project.GetTextVars();

    project::TextVariables reply;
    auto map = reply.mutable_variables();

    for( const auto& [key, value] : vars )
        ( *map )[ std::string( key.ToUTF8() ) ] = value.ToUTF8();

    return reply;
}


HANDLER_RESULT<Empty> API_HANDLER_COMMON::handleSetTextVariables(
    const HANDLER_CONTEXT<SetTextVariables>& aCtx )
{
    if( !aCtx.Request.has_document() || aCtx.Request.document().type() != DOCTYPE_PROJECT )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNHANDLED );
        // No error message, this is a flag that the server should try a different handler
        return tl::unexpected( e );
    }

    PROJECT& project = Pgm().GetSettingsManager().Prj();

    if( project.IsNullProject() )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_NOT_READY );
        e.set_error_message( "no valid project is loaded, cannot set text variables" );
        return tl::unexpected( e );
    }

    const project::TextVariables& newVars = aCtx.Request.variables();
    std::map<wxString, wxString>& vars = project.GetTextVars();

    if( aCtx.Request.merge_mode() == MapMergeMode::MMM_REPLACE )
        vars.clear();

    for( const auto& [key, value] : newVars.variables() )
        vars[wxString::FromUTF8( key )] = wxString::FromUTF8( value );

    Pgm().GetSettingsManager().SaveProject();

    return Empty();
}


HANDLER_RESULT<OpenDocumentResponse> API_HANDLER_COMMON::handleOpenDocument(
        const HANDLER_CONTEXT<OpenDocument>& aCtx )
{
    if( !m_openDocumentHandler )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNIMPLEMENTED );
        e.set_error_message( "OpenDocument is not available in this KiCad mode" );
        return tl::unexpected( e );
    }

    return m_openDocumentHandler( aCtx.Request );
}


HANDLER_RESULT<Empty> API_HANDLER_COMMON::handleCloseDocument(
        const HANDLER_CONTEXT<CloseDocument>& aCtx )
{
    if( !m_closeDocumentHandler )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNIMPLEMENTED );
        e.set_error_message( "CloseDocument is not available in this KiCad mode" );
        return tl::unexpected( e );
    }

    return m_closeDocumentHandler( aCtx.Request );
}


HANDLER_RESULT<Empty> API_HANDLER_COMMON::handleCloseAllDocuments( const HANDLER_CONTEXT<CloseAllDocuments>& aCtx )
{
    if( !m_closeAllDocumentsHandler )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_UNIMPLEMENTED );
        e.set_error_message( "CloseAllDocuments is not available in this KiCad mode" );
        return tl::unexpected( e );
    }

    return m_closeAllDocumentsHandler( aCtx.Request );
}


namespace
{

/**
 * Proto parameter <-> TRANSLINE_PARAMETERS.
 *
 * Kept as one table so the two directions cannot drift: the response echoes
 * inputs back alongside results, so a mapping correct in only one direction
 * would silently drop values.
 */
const std::vector<std::pair<commands::TransmissionLineParameter, TRANSLINE_PARAMETERS>>&
transLineParamMap()
{
    static const std::vector<std::pair<commands::TransmissionLineParameter, TRANSLINE_PARAMETERS>>
            map = {
                { commands::TLP_EPSILON_R, TRANSLINE_PARAMETERS::EPSILONR },
                { commands::TLP_TAN_D, TRANSLINE_PARAMETERS::TAND },
                { commands::TLP_RHO, TRANSLINE_PARAMETERS::RHO },
                { commands::TLP_SUBSTRATE_HEIGHT, TRANSLINE_PARAMETERS::H },
                { commands::TLP_TOP_HEIGHT, TRANSLINE_PARAMETERS::H_T },
                { commands::TLP_CONDUCTOR_THICKNESS, TRANSLINE_PARAMETERS::T },
                { commands::TLP_ROUGHNESS, TRANSLINE_PARAMETERS::ROUGH },
                { commands::TLP_MU_R, TRANSLINE_PARAMETERS::MUR },
                { commands::TLP_MU_R_CONDUCTOR, TRANSLINE_PARAMETERS::MURC },
                { commands::TLP_PHYS_WIDTH, TRANSLINE_PARAMETERS::PHYS_WIDTH },
                { commands::TLP_PHYS_SPACING, TRANSLINE_PARAMETERS::PHYS_S },
                { commands::TLP_PHYS_LENGTH, TRANSLINE_PARAMETERS::PHYS_LEN },
                { commands::TLP_PHYS_DIAMETER_IN, TRANSLINE_PARAMETERS::PHYS_DIAM_IN },
                { commands::TLP_PHYS_DIAMETER_OUT, TRANSLINE_PARAMETERS::PHYS_DIAM_OUT },
                { commands::TLP_STRIPLINE_A, TRANSLINE_PARAMETERS::STRIPLINE_A },
                { commands::TLP_TWISTS_PER_LENGTH, TRANSLINE_PARAMETERS::TWISTEDPAIR_TWIST },
                { commands::TLP_TWISTEDPAIR_EPSILON_R_ENV,
                  TRANSLINE_PARAMETERS::TWISTEDPAIR_EPSILONR_ENV },
                { commands::TLP_FREQUENCY, TRANSLINE_PARAMETERS::FREQUENCY },
                { commands::TLP_Z0, TRANSLINE_PARAMETERS::Z0 },
                { commands::TLP_Z0_EVEN, TRANSLINE_PARAMETERS::Z0_E },
                { commands::TLP_Z0_ODD, TRANSLINE_PARAMETERS::Z0_O },
                { commands::TLP_Z_DIFF, TRANSLINE_PARAMETERS::Z_DIFF },
                { commands::TLP_Z_COMMON, TRANSLINE_PARAMETERS::Z_COMM },
                { commands::TLP_ELECTRICAL_LENGTH, TRANSLINE_PARAMETERS::ANG_L },
                { commands::TLP_EPSILON_EFF, TRANSLINE_PARAMETERS::EPSILON_EFF },
                { commands::TLP_UNIT_PROP_DELAY, TRANSLINE_PARAMETERS::UNIT_PROP_DELAY },
                { commands::TLP_LOSS_CONDUCTOR, TRANSLINE_PARAMETERS::LOSS_CONDUCTOR },
                { commands::TLP_LOSS_DIELECTRIC, TRANSLINE_PARAMETERS::LOSS_DIELECTRIC },
                { commands::TLP_SKIN_DEPTH, TRANSLINE_PARAMETERS::SKIN_DEPTH },
                { commands::TLP_CUTOFF_FREQUENCY, TRANSLINE_PARAMETERS::CUTOFF_FREQUENCY },
                { commands::TLP_SIGMA, TRANSLINE_PARAMETERS::SIGMA },
                { commands::TLP_ATTEN_CONDUCTOR, TRANSLINE_PARAMETERS::ATTEN_COND },
                { commands::TLP_ATTEN_DIELECTRIC, TRANSLINE_PARAMETERS::ATTEN_DILECTRIC },
                { commands::TLP_EPSILON_EFF_EVEN, TRANSLINE_PARAMETERS::EPSILON_EFF_EVEN },
                { commands::TLP_EPSILON_EFF_ODD, TRANSLINE_PARAMETERS::EPSILON_EFF_ODD },
                { commands::TLP_UNIT_PROP_DELAY_EVEN, TRANSLINE_PARAMETERS::UNIT_PROP_DELAY_EVEN },
                { commands::TLP_UNIT_PROP_DELAY_ODD, TRANSLINE_PARAMETERS::UNIT_PROP_DELAY_ODD },
                { commands::TLP_COUPLING_K, TRANSLINE_PARAMETERS::COUPLING_K },
                { commands::TLP_DIELECTRIC_MODEL, TRANSLINE_PARAMETERS::DIELECTRIC_MODEL_SEL },
                { commands::TLP_EPSILON_R_SPEC_FREQ, TRANSLINE_PARAMETERS::EPSILONR_SPEC_FREQ },
                { commands::TLP_SOLDERMASK_PRESENT, TRANSLINE_PARAMETERS::SOLDERMASK_PRESENT },
                { commands::TLP_SOLDERMASK_THICKNESS, TRANSLINE_PARAMETERS::SOLDERMASK_THICKNESS },
                { commands::TLP_SOLDERMASK_EPSILON_R, TRANSLINE_PARAMETERS::SOLDERMASK_EPSILONR },
                { commands::TLP_SOLDERMASK_TAND, TRANSLINE_PARAMETERS::SOLDERMASK_TAND },
                { commands::TLP_CPW_BACKMETAL, TRANSLINE_PARAMETERS::CPW_BACKMETAL },
            };

    return map;
}


std::optional<TRANSLINE_PARAMETERS> toTransLineParam( commands::TransmissionLineParameter aParam )
{
    for( const auto& [proto, native] : transLineParamMap() )
    {
        if( proto == aParam )
            return native;
    }

    return std::nullopt;
}


std::unique_ptr<TRANSLINE_CALCULATION_BASE> makeTransLine( commands::TransmissionLineType aType )
{
    switch( aType )
    {
    case commands::TLT_MICROSTRIP:             return std::make_unique<MICROSTRIP>();
    case commands::TLT_COUPLED_MICROSTRIP:     return std::make_unique<COUPLED_MICROSTRIP>();
    case commands::TLT_STRIPLINE:              return std::make_unique<STRIPLINE>();
    case commands::TLT_COUPLED_STRIPLINE:      return std::make_unique<COUPLED_STRIPLINE>();
    case commands::TLT_COPLANAR:               return std::make_unique<COPLANAR>();
    case commands::TLT_COAX:                   return std::make_unique<COAX>();
    case commands::TLT_TWISTED_PAIR:           return std::make_unique<TWISTEDPAIR>();
    case commands::TLT_RECTANGULAR_WAVEGUIDE:  return std::make_unique<RECTWAVEGUIDE>();
    default:                                   return nullptr;
    }
}

} // namespace


HANDLER_RESULT<commands::RunTransmissionLineCalculationResponse>
API_HANDLER_COMMON::handleRunTransmissionLineCalculation(
        const HANDLER_CONTEXT<commands::RunTransmissionLineCalculation>& aCtx )
{
    std::unique_ptr<TRANSLINE_CALCULATION_BASE> calc = makeTransLine( aCtx.Request.type() );

    if( !calc )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "unknown or unset transmission line type" );
        return tl::unexpected( e );
    }

    for( const commands::TransmissionLineValue& value : aCtx.Request.parameters() )
    {
        std::optional<TRANSLINE_PARAMETERS> param = toTransLineParam( value.parameter() );

        if( !param )
        {
            ApiResponseStatus e;
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message( fmt::format( "unhandled transmission line parameter {}",
                                              (int) value.parameter() ) );
            return tl::unexpected( e );
        }

        // TRANSLINE_CALCULATION_BASE stores parameters in a map populated per
        // calculator and reads it with .at(), so setting one a given geometry
        // does not model throws. Report that as a bad request rather than
        // letting it escape the handler -- an exception here leaves the server
        // never replying, which the client only sees as a timeout.
        try
        {
            calc->SetParameter( *param, value.value() );
        }
        catch( const std::out_of_range& )
        {
            ApiResponseStatus e;
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message(
                    fmt::format( "parameter {} does not apply to this transmission line type",
                                 commands::TransmissionLineParameter_Name( value.parameter() ) ) );
            return tl::unexpected( e );
        }
    }

    commands::RunTransmissionLineCalculationResponse response;

    try
    {
    if( aCtx.Request.mode() == commands::TLM_SYNTHESISE )
    {
        std::optional<TRANSLINE_PARAMETERS> target =
                toTransLineParam( aCtx.Request.synthesize_target() );

        if( !target )
        {
            ApiResponseStatus e;
            e.set_status( ApiStatusCode::AS_BAD_REQUEST );
            e.set_error_message( "synthesize_target is required for TLM_SYNTHESISE" );
            return tl::unexpected( e );
        }

        SYNTHESIZE_OPTS opts = SYNTHESIZE_OPTS::DEFAULT;

        switch( aCtx.Request.synthesis_option() )
        {
        case commands::TLSO_FIX_WIDTH:        opts = SYNTHESIZE_OPTS::FIX_WIDTH; break;
        case commands::TLSO_FIX_SPACING:      opts = SYNTHESIZE_OPTS::FIX_SPACING; break;
        case commands::TLSO_FROM_ZDIFF_ZCOMM: opts = SYNTHESIZE_OPTS::FROM_ZDIFF_ZCOMM; break;
        default:                              break;
        }

        calc->SetSynthesizeTarget( *target );
        response.set_converged( calc->Synthesize( opts ) );
    }
    else
    {
        // Analysis has no failure mode: it evaluates closed-form expressions.
        calc->Analyse();
        response.set_converged( true );
    }
    }
    catch( const std::exception& exc )
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( fmt::format( "transmission line calculation failed: {}",
                                          exc.what() ) );
        return tl::unexpected( e );
    }

    // Echo every parameter back, inputs included: a caller that synthesised a
    // width wants the resulting impedance in the same reply.
    for( const auto& [proto, native] : transLineParamMap() )
    {
        // Only report what this calculator actually models. A microstrip has no
        // even/odd impedance and no inner diameter, and asking for them throws.
        try
        {
            const double value = calc->GetParameter( native );

            commands::TransmissionLineValue* out = response.add_parameters();
            out->set_parameter( proto );
            out->set_value( value );
        }
        catch( const std::out_of_range& )
        {
            continue;
        }
    }

    return response;
}


HANDLER_RESULT<ListLibrariesResponse> API_HANDLER_COMMON::handleListLibraries(
        const HANDLER_CONTEXT<ListLibraries>& aCtx )
{
    LIBRARY_TABLE_TYPE type;

    switch( aCtx.Request.type() )
    {
    case LBT_SYMBOL:       type = LIBRARY_TABLE_TYPE::SYMBOL;       break;
    case LBT_FOOTPRINT:    type = LIBRARY_TABLE_TYPE::FOOTPRINT;    break;
    case LBT_DESIGN_BLOCK: type = LIBRARY_TABLE_TYPE::DESIGN_BLOCK; break;

    default:
    {
        ApiResponseStatus e;
        e.set_status( ApiStatusCode::AS_BAD_REQUEST );
        e.set_error_message( "a library type is required: symbol, footprint or design block" );
        return tl::unexpected( e );
    }
    }

    LIBRARY_TABLE_SCOPE scope;

    switch( aCtx.Request.scope() )
    {
    case LBS_GLOBAL:  scope = LIBRARY_TABLE_SCOPE::GLOBAL;  break;
    case LBS_PROJECT: scope = LIBRARY_TABLE_SCOPE::PROJECT; break;
    default:          scope = LIBRARY_TABLE_SCOPE::BOTH;    break;
    }

    LIBRARY_MANAGER& mgr = Pgm().GetLibraryManager();
    ListLibrariesResponse response;

    // aIncludeInvalid: a row KiCad could not validate is exactly what a caller needs to
    // see -- silently dropping it turns a misconfigured library into a missing one.
    for( const LIBRARY_TABLE_ROW* row : mgr.Rows( type, scope, true ) )
    {
        if( !row )
            continue;

        LibraryInfo* out = response.add_libraries();
        out->set_nickname( row->Nickname().ToStdString() );
        out->set_description( row->Description().ToStdString() );
        out->set_uri( row->URI().ToStdString() );
        out->set_resolved_uri( LIBRARY_MANAGER::GetFullURI( row, true ).ToStdString() );
        out->set_plugin_type( row->Type().ToStdString() );
        out->set_enabled( !row->Disabled() );
        out->set_visible( !row->Hidden() );
        out->set_ok( row->IsOk() );
        out->set_error( row->ErrorDescription().ToStdString() );

        switch( row->Scope() )
        {
        case LIBRARY_TABLE_SCOPE::GLOBAL:  out->set_scope( LBS_GLOBAL );  break;
        case LIBRARY_TABLE_SCOPE::PROJECT: out->set_scope( LBS_PROJECT ); break;
        default:                           out->set_scope( LBS_UNKNOWN ); break;
        }
    }

    return response;
}
