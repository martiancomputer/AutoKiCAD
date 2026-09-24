/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
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

/**
 * @file autoroute_hello_main.cpp
 *
 * Headless routing "hello world": load a board, sync it into a PNS world, route
 * one unrouted connection, write the board back out.
 *
 * The point is to establish the write-back path, not to route well. PNS already
 * *computes* headlessly -- qa_pns_regressions proves that -- but
 * PNS_KICAD_IFACE_BASE::AddItem() is an empty stub, so nothing produced by a
 * headless router ever reaches the BOARD. Item creation lives in
 * PNS_KICAD_IFACE (the GUI subclass) and depends on state that only exists
 * there: m_fpOffsets, m_itemGroups, m_replacementMap.
 *
 * HEADLESS_PNS_IFACE below fills that gap for the three item kinds routing
 * actually emits (segment, arc, via). It deliberately does not reproduce the
 * GUI's group/footprint-offset semantics -- those matter for interactive
 * dragging, not for laying down a fresh track.
 *
 *   pns_autoroute_hello <board.kicad_pcb> [-o out.kicad_pcb] [-n count] [-v]
 */

#include <algorithm>
#include <limits>
#include <set>
#include <cstdio>
#include <memory>
#include <string>
#include <vector>

#include <wx/app.h>
#include <wx/cmdline.h>
#include <wx/init.h>
#include <wx/string.h>

#include <board.h>
#include <board_design_settings.h>
#include <connectivity/connectivity_data.h>
#include <connectivity/connectivity_items.h>
#include <ratsnest/ratsnest_data.h>
#include <netclass.h>
#include <netinfo.h>
#include <pgm_base.h>
#include <pcb_track.h>
#include <pad.h>
#include <footprint.h>
#include <pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.h>
#include <drc/drc_engine.h>
#include <wildcards_and_files_ext.h>
#include <settings/settings_manager.h>
#include <project.h>

#include <router/pns_router.h>
#include <router/pns_kicad_iface.h>
#include <router/pns_routing_settings.h>
#include <router/pns_item.h>
#include <router/pns_segment.h>
#include <router/pns_arc.h>
#include <router/pns_via.h>
#include <router/pns_solid.h>
#include <router/pns_node.h>
#include <router/pns_placement_algo.h>
#include <router/pns_itemset.h>
#include <router/pns_line.h>
#include <router/pns_meander.h>
#include <router/pns_meander_placer_base.h>


/**
 * Minimal write-back interface for headless routing.
 *
 * Everything else -- world sync, rule resolution, layer mapping, net lookup --
 * is inherited unchanged from PNS_KICAD_IFACE_BASE, which is already frame-free
 * and has no pure virtuals.
 */
class HEADLESS_PNS_IFACE : public PNS_KICAD_IFACE_BASE
{
public:
    HEADLESS_PNS_IFACE() = default;

    void AddItem( PNS::ITEM* aItem ) override
    {
        BOARD_CONNECTED_ITEM* boardItem = createBoardItem( aItem );

        if( !boardItem )
            return;

        aItem->SetParent( boardItem );
        boardItem->ClearFlags();
        m_board->Add( boardItem, ADD_MODE::APPEND );
        m_added.push_back( boardItem );
    }

    void RemoveItem( PNS::ITEM* aItem ) override
    {
        if( BOARD_ITEM* parent = aItem->Parent() )
        {
            // Pads and other solids are board furniture, not routing output --
            // the router only ever "removes" them as part of a drag.
            if( aItem->OfKind( PNS::ITEM::SOLID_T ) )
                return;

            m_board->Remove( parent, REMOVE_MODE::BULK );
            m_removed.push_back( parent );
        }
    }

    void UpdateItem( PNS::ITEM* aItem ) override
    {
        RemoveItem( aItem );
        AddItem( aItem );
    }

    void Commit() override
    {
        // Connectivity is stale after adding copper; rebuilding it here means a
        // caller can immediately ask how many connections remain.
        m_board->BuildConnectivity();
    }

    int AddedCount() const { return (int) m_added.size(); }
    int RemovedCount() const { return (int) m_removed.size(); }

    /**
     * Undo everything added since `aMark` (a previous AddedCount()).
     *
     * Needed because a route can succeed as far as PNS is concerned while
     * closing no connection at all -- a track laid on a layer neither pad
     * reaches is dead copper. Without rollback those attempts accumulate.
     */
    void RollbackTo( int aMark )
    {
        while( (int) m_added.size() > aMark )
        {
            BOARD_ITEM* item = m_added.back();
            m_added.pop_back();
            m_board->Remove( item, REMOVE_MODE::BULK );
            delete item;
        }

        m_board->BuildConnectivity();
    }

private:
    /**
     * Turn a router item into a board item.
     *
     * Covers only what routing emits. PNS_KICAD_IFACE::createBoardItem() also
     * handles group membership and footprint offsets, which require subclass
     * state and are meaningless for a freshly placed track.
     */
    BOARD_CONNECTED_ITEM* createBoardItem( PNS::ITEM* aItem )
    {
        NETINFO_ITEM* net = static_cast<NETINFO_ITEM*>( aItem->Net() );

        if( !net )
            net = NETINFO_LIST::OrphanedItem();

        switch( aItem->Kind() )
        {
        case PNS::ITEM::SEGMENT_T:
        {
            PNS::SEGMENT* seg = static_cast<PNS::SEGMENT*>( aItem );
            PCB_TRACK*    track = new PCB_TRACK( m_board );

            const SEG& s = seg->Seg();
            track->SetStart( s.A );
            track->SetEnd( s.B );
            track->SetWidth( seg->Width() );
            track->SetLayer( GetBoardLayerFromPNSLayer( seg->Layers().Start() ) );
            track->SetNet( net );
            return track;
        }

        case PNS::ITEM::ARC_T:
        {
            PNS::ARC* arc = static_cast<PNS::ARC*>( aItem );
            PCB_ARC*  newArc =
                    new PCB_ARC( m_board, static_cast<const SHAPE_ARC*>( arc->Shape( -1 ) ) );

            newArc->SetWidth( arc->Width() );
            newArc->SetLayer( GetBoardLayerFromPNSLayer( arc->Layers().Start() ) );
            newArc->SetNet( net );
            return newArc;
        }

        case PNS::ITEM::VIA_T:
        {
            PNS::VIA* via = static_cast<PNS::VIA*>( aItem );
            PCB_VIA*  newVia = new PCB_VIA( m_board );

            newVia->SetPosition( via->Pos() );
            newVia->SetWidth( PADSTACK::ALL_LAYERS, via->Diameter( 0 ) );
            newVia->SetDrill( via->Drill() );
            newVia->SetNet( net );

            PCB_LAYER_ID top = GetBoardLayerFromPNSLayer( via->Layers().Start() );
            PCB_LAYER_ID bottom = GetBoardLayerFromPNSLayer( via->Layers().End() );
            newVia->SetLayerPair( top, bottom );
            return newVia;
        }

        default:
            return nullptr;
        }
    }

    std::vector<BOARD_ITEM*> m_added;
    std::vector<BOARD_ITEM*> m_removed;
};


namespace
{

int unroutedCount( BOARD* aBoard )
{
    aBoard->BuildConnectivity();
    return (int) aBoard->GetConnectivity()->GetUnconnectedCount( true );
}


/**
 * One ratsnest anchor pair that still needs routing.
 */
struct TARGET
{
    VECTOR2I     a;
    VECTOR2I     b;
    int          netCode = -1;
    PCB_LAYER_ID layer = F_Cu;
    bool         valid = false;

    // The board items the ratsnest anchors belong to. Looking the router item up
    // by parent is reliable; a positional HitTest at the anchor is not, and
    // DIFF_PAIR_PLACER refuses to start without a real start item.
    const BOARD_CONNECTED_ITEM* parentA = nullptr;
    const BOARD_CONNECTED_ITEM* parentB = nullptr;
};


std::vector<TARGET> collectTargets( BOARD* aBoard )
{
    std::vector<TARGET> out;
    std::shared_ptr<CONNECTIVITY_DATA> conn = aBoard->GetConnectivity();

    for( const NETINFO_ITEM* netInfo : aBoard->GetNetInfo() )
    {
        if( !netInfo || netInfo->GetNetCode() <= 0 )
            continue;

        RN_NET* net = conn->GetRatsnestForNet( netInfo->GetNetCode() );

        if( !net )
            continue;

        for( const CN_EDGE& edge : net->GetEdges() )
        {
            std::shared_ptr<const CN_ANCHOR> src = edge.GetSourceNode();
            std::shared_ptr<const CN_ANCHOR> dst = edge.GetTargetNode();

            if( !src || !dst || src->Dirty() || dst->Dirty() )
                continue;

            const BOARD_CONNECTED_ITEM* parent = src->Parent();

            if( !parent )
                continue;

            TARGET t;
            t.a = src->Pos();
            t.b = dst->Pos();
            t.netCode = parent->GetNetCode();
            t.parentA = parent;
            t.parentB = dst->Parent();

            LSET layers = parent->GetLayerSet() & LSET::AllCuMask();
            t.layer = layers.any() ? layers.Seq().front() : F_Cu;
            t.valid = true;
            out.push_back( t );
        }
    }

    // Shortest first. Ordering is the single biggest lever on autorouter quality:
    // short connections are the most constrained and the least likely to be
    // blocked later, so committing them early leaves more room for the rest.
    std::sort( out.begin(), out.end(),
               []( const TARGET& l, const TARGET& r )
               {
                   return ( l.b - l.a ).SquaredEuclideanNorm()
                          < ( r.b - r.a ).SquaredEuclideanNorm();
               } );

    return out;
}


} // namespace


/**
 * One routing attempt with a specific mode and layer.
 *
 * Returns true when copper was committed. PNS is left with routing stopped
 * either way, so the caller can retry with different settings.
 */
bool tryRoute( PNS::ROUTER& aRouter, HEADLESS_PNS_IFACE& aIface, const TARGET& aTarget,
               PNS::PNS_MODE aMode, PCB_LAYER_ID aLayer, bool aForceFinish,
               PNS::ROUTER_MODE aRouterMode = PNS::PNS_MODE_ROUTE_SINGLE,
               wxString* aWhy = nullptr, BOARD* aBoard = nullptr )
{
    auto note = [&]( const wxString& aMsg ) { if( aWhy ) *aWhy = aMsg; };
    // SetMode() picks the placer (LINE_PLACER vs DIFF_PAIR_PLACER); the settings
    // mode below is the obstacle strategy (walkaround vs shove). Two different
    // knobs with confusingly similar names.
    aRouter.SetMode( aRouterMode );
    aRouter.Settings().SetMode( aMode );

    const int pnsLayer = aIface.GetPNSLayerFromBoardLayer( aLayer );

    auto itemUnder =
            [&]( const VECTOR2I& aPos, const BOARD_CONNECTED_ITEM* aParent ) -> PNS::ITEM*
            {
                PNS::NODE* world = aRouter.GetWorld();

                if( !world )
                    return nullptr;

                // Prefer an exact parent lookup: the ratsnest told us which pad
                // this anchor belongs to, so there is no need to guess by position.
                if( aParent )
                {
                    if( PNS::ITEM* byParent = world->FindItemByParent( aParent ) )
                        return byParent;
                }

                // Named local: HitTest() returns by value and CItems() hands back a
                // reference into it, which a range-for does not lifetime-extend.
                const PNS::ITEM_SET hit = world->HitTest( aPos );

                for( PNS::ITEM* it : hit.CItems() )
                {
                    if( it->Layers().Overlaps( pnsLayer ) )
                        return it;
                }

                return nullptr;
            };

    PNS::ITEM* startItem = itemUnder( aTarget.a, aTarget.parentA );

    if( !startItem )
    {
        note( wxT( "no start item" ) );
        return false;
    }

    if( !aRouter.StartRouting( aTarget.a, startItem, pnsLayer ) )
    {
        note( wxString::Format( wxT( "StartRouting: %s" ), aRouter.FailureReason() ) );
        return false;
    }

    PNS::ITEM* endItem = itemUnder( aTarget.b, aTarget.parentB );

    // A differential pair has to be aimed between *both* destination pads. Moving
    // to one pad's centre leaves the placer unable to fit the pair, so m_fitOk
    // stays false and FixRoute refuses.
    VECTOR2I endPoint = aTarget.b;

    if( aRouterMode == PNS::PNS_MODE_ROUTE_DIFF_PAIR && aBoard )
    {
        NETINFO_ITEM* net = aBoard->FindNet( aTarget.netCode );
        NETINFO_ITEM* coupled = net ? aBoard->DpCoupledNet( net ) : nullptr;

        if( coupled )
        {
            const PAD* nearest = nullptr;
            double     bestDist = std::numeric_limits<double>::max();

            for( FOOTPRINT* fp : aBoard->Footprints() )
            {
                for( PAD* pad : fp->Pads() )
                {
                    if( pad->GetNetCode() != coupled->GetNetCode() )
                        continue;

                    const double dist = ( pad->GetPosition() - aTarget.b ).EuclideanNorm();

                    if( dist < bestDist )
                    {
                        bestDist = dist;
                        nearest = pad;
                    }
                }
            }

            if( nearest )
                endPoint = ( aTarget.b + nearest->GetPosition() ) / 2;
        }
    }

    // Move()'s return value matters: when it fails the trace is left empty
    // (seg=0), and FixRoute then bails on the segment-count guard rather than on
    // anything to do with fit. Try the pair midpoint first, then the raw anchor.
    std::vector<VECTOR2I> endCandidates{ endPoint };

    if( endPoint != aTarget.b )
        endCandidates.push_back( aTarget.b );

    bool moved = false;

    for( const VECTOR2I& candidate : endCandidates )
    {
        // Walk toward the target in steps rather than jumping straight to it.
        // ROUTER_TOOL drives the placer this way interactively (one Move per
        // mouse motion), and DIFF_PAIR_PLACER's gateway fitting appears to
        // depend on it: a single jump leaves tryWalkDp() returning true with an
        // emptied trace. Cheap to try, and it distinguishes "we drive it wrong"
        // from "upstream is broken".
        const int steps = aRouterMode == PNS::PNS_MODE_ROUTE_DIFF_PAIR ? 8 : 1;
        bool      lastOk = false;

        for( int i = 1; i <= steps; i++ )
        {
            const VECTOR2I waypoint =
                    aTarget.a + ( candidate - aTarget.a ) * i / steps;

            lastOk = aRouter.Move( waypoint, i == steps ? endItem : nullptr );
        }

        if( lastOk )
        {
            endPoint = candidate;
            moved = true;
            break;
        }
    }

    if( !moved )
    {
        note( wxT( "Move produced no trace" ) );
        aRouter.StopRouting();
        return false;
    }

    if( !aRouter.FixRoute( endPoint, endItem, aForceFinish, true ) )
    {
        // DIFF_PAIR_PLACER::FixRoute bails when m_fitOk is false, which means the
        // P or N line collided during Move(). m_fitOk is private, but the traces
        // and the node it fitted against are public, so ask the world directly
        // what the pair ran into.
        wxString detail;

        if( PNS::PLACEMENT_ALGO* placer = aRouter.Placer() )
        {
            const PNS::ITEM_SET traces = placer->Traces();
            PNS::NODE*          node = placer->CurrentNode( true );

            if( traces.Size() == 0 )
            {
                detail = wxT( "; placer produced no trace" );
            }
            else if( node )
            {
                int          collisions = 0;
                PNS::ITEM*   firstHit = nullptr;

                for( const PNS::ITEM* trace : traces.CItems() )
                {
                    if( auto obs = node->CheckColliding( trace, PNS::ITEM::ANY_T ) )
                    {
                        collisions++;

                        if( !firstHit )
                            firstHit = obs->m_item;
                    }
                }

                if( collisions )
                {
                    detail = wxString::Format(
                            wxT( "; %d of %d trace(s) collide, first with net %d" ),
                            collisions, traces.Size(),
                            firstHit ? aRouter.GetInterface()->GetNetCode( firstHit->Net() ) : -1 );
                }
                else
                {
                    // No collision means m_fitOk was not the blocker. The other
                    // early exit in DIFF_PAIR_PLACER::FixRoute is a P or N chain
                    // with fewer than one segment, so report the geometry.
                    wxString shape;

                    for( const PNS::ITEM* trace : traces.CItems() )
                    {
                        if( const auto* line = dynamic_cast<const PNS::LINE*>( trace ) )
                        {
                            shape += wxString::Format( wxT( " [seg=%d pts=%d len=%.3fmm]" ),
                                                       line->SegmentCount(),
                                                       line->PointCount(),
                                                       line->CLine().Length() / 1e6 );
                        }
                        else
                        {
                            shape += wxString::Format( wxT( " [kind=%d]" ),
                                                       (int) trace->Kind() );
                        }
                    }

                    detail = wxString::Format( wxT( "; %d trace(s), no collision:%s" ),
                                               traces.Size(), shape );
                }
            }
        }

        note( wxString::Format( wxT( "FixRoute failed (end item %s)%s" ),
                                endItem ? wxT( "found" ) : wxT( "none" ), detail ) );
        aRouter.StopRouting();
        return false;
    }

    // CommitRouting() only commits while still in ROUTE_TRACK and stops routing
    // itself; stopping first silently discards the placement.
    aRouter.CommitRouting();
    return true;
}


/**
 * Lengthen one net with meanders until it reaches `aTargetLength`.
 *
 * This is the first thing in the router that lets a *constraint* drive where
 * copper goes, rather than only grading it afterwards. PNS already has the
 * machinery -- PNS_MODE_TUNE_SINGLE selects MEANDER_PLACER -- it simply had no
 * headless caller. Sequence mirrors pcb_tuning_pattern.cpp.
 */
bool tuneNet( PNS::ROUTER& aRouter, BOARD* aBoard, int aNetCode, long long int aTargetLength,
              long long int* aResult )
{
    // Tune the longest track of the net: meanders need room, and the longest
    // run is the most likely to have it.
    PCB_TRACK* best = nullptr;

    for( PCB_TRACK* track : aBoard->Tracks() )
    {
        if( track->Type() != PCB_TRACE_T || track->GetNetCode() != aNetCode )
            continue;

        if( !best || track->GetLength() > best->GetLength() )
            best = track;
    }

    if( !best )
        return false;

    aRouter.SetMode( PNS::PNS_MODE_TUNE_SINGLE );

    PNS::NODE* world = aRouter.GetWorld();

    if( !world )
        return false;

    PNS::ITEM* startItem = world->FindItemByParent( best );

    if( !startItem )
        return false;

    const int pnsLayer = startItem->Layers().Start();

    if( !aRouter.StartRouting( best->GetStart(), startItem, pnsLayer ) )
        return false;

    auto* placer = dynamic_cast<PNS::MEANDER_PLACER_BASE*>( aRouter.Placer() );

    if( !placer )
    {
        aRouter.StopRouting();
        return false;
    }

    PNS::MEANDER_SETTINGS settings = placer->MeanderSettings();
    settings.m_keepEndpoints = true;   // required so the tuned run still lands on its pads

    // The netclass rides on the settings, and the delay calculation dereferences
    // it without a null check (tuning_profile_parameters_user_defined.cpp:42).
    // Leaving it unset is a segfault, not a fallback.
    settings.m_netClass = const_cast<NETCLASS*>( best->GetEffectiveNetClass() );

    if( !settings.m_netClass )
    {
        aRouter.StopRouting();
        return false;
    }

    settings.SetTargetLength( aTargetLength );
    placer->UpdateSettings( settings );

    aRouter.Move( best->GetEnd(), nullptr );

    if( !aRouter.FixRoute( best->GetEnd(), nullptr, false, true ) )
    {
        aRouter.StopRouting();
        return false;
    }

    if( aResult )
        *aResult = placer->TuningLengthResult();

    aRouter.CommitRouting();
    return true;
}


long long int netLength( BOARD* aBoard, int aNetCode )
{
    long long int total = 0;

    for( PCB_TRACK* track : aBoard->Tracks() )
    {
        if( track->GetNetCode() == aNetCode )
            total += (long long int) track->GetLength();
    }

    return total;
}


int main( int argc, char* argv[] )
{
    wxInitializer initializer;

    // BOARD::CacheTriangulation() -- reached from BuildConnectivity() -- submits
    // to the global thread pool. A PGM_BASE is registered globally by the QA app
    // scaffolding, so GetKiCadThreadPool() takes the "use Pgm's pool" branch
    // rather than lazily creating its own; without InitPgm() that pool is null
    // and the first submit segfaults. qa_pns_regressions never hits this because
    // it never builds connectivity.
    Pgm().InitPgm( true, true );

    static const wxCmdLineEntryDesc desc[] = {
        { wxCMD_LINE_SWITCH, "h", "help", "show this help", wxCMD_LINE_VAL_NONE,
          wxCMD_LINE_OPTION_HELP },
        { wxCMD_LINE_OPTION, "o", "output", "write the routed board here",
          wxCMD_LINE_VAL_STRING },
        { wxCMD_LINE_OPTION, "n", "count", "connections to route (default 1)",
          wxCMD_LINE_VAL_NUMBER },
        { wxCMD_LINE_SWITCH, "f", "force-finish", "commit routes PNS would reject",
          wxCMD_LINE_VAL_NONE },
        { wxCMD_LINE_SWITCH, "t", "tune", "meander short nets up to the longest",
          wxCMD_LINE_VAL_NONE },
        { wxCMD_LINE_PARAM, nullptr, nullptr, "board.kicad_pcb", wxCMD_LINE_VAL_STRING },
        { wxCMD_LINE_NONE }
    };

    wxCmdLineParser parser( desc, argc, argv );

    if( parser.Parse() != 0 )
        return 1;

    const wxString boardPath = parser.GetParam( 0 );
    wxString       outPath;
    long           wanted = 1;

    parser.Found( "o", &outPath );
    parser.Found( "n", &wanted );
    const bool forceFinish = parser.Found( "f" );
    const bool tune = parser.Found( "t" );

    // Netclasses live in the *project*, not the board. Without one,
    // NETINFO_ITEM::GetEffectiveNetClass() is null and the length/delay
    // calculation dereferences it the moment the meander placer asks for a path
    // delay. BOARD_LOADER does this via SetProject(); PCB_IO alone does not.
    SETTINGS_MANAGER settingsManager;
    wxFileName       projectFile( boardPath );
    projectFile.SetExt( FILEEXT::ProjectFileExtension );

    const bool haveProject = projectFile.Exists()
                             && settingsManager.LoadProject( projectFile.GetFullPath() );

    if( !haveProject )
    {
        printf( "warning: no project file beside the board; netclass-dependent "
                "features (length tuning) are unavailable\n" );
    }

    std::unique_ptr<BOARD> board;

    try
    {
        PCB_IO_KICAD_SEXPR io;
        board.reset( io.LoadBoard( boardPath, nullptr, nullptr ) );
    }
    catch( const std::exception& exc )
    {
        printf( "failed to load %s: %s\n", (const char*) boardPath.utf8_str(), exc.what() );
        return 2;
    }

    if( !board )
    {
        printf( "failed to load %s\n", (const char*) boardPath.utf8_str() );
        return 2;
    }

    if( haveProject )
        board->SetProject( &settingsManager.Prj() );

    // Finish the setup BOARD_LOADER normally performs. Loading through PCB_IO
    // yields a parsed board, not a *ready* one: netclasses are not bound to nets
    // and tuning-profile parents are unset, so the length/delay calculation
    // dereferences uninitialised netclass state and segfaults the moment the
    // meander placer asks for a path delay.
    board->BuildListOfNets();
    board->BuildConnectivity();
    board->SynchronizeNetsAndNetClasses( true );
    board->SynchronizeTuningProfileProperties();

    // Create and initialise the DRC engine. PNS_PCBNEW_RULE_RESOLVER resolves every
    // clearance through m_DRCEngine->EvalRules(); BOARD_LOADER normally builds it
    // (board_loader.cpp), but loading straight through PCB_IO bypasses that, leaving
    // the router with no design rules and free to place copper on top of other nets.
    {
        BOARD_DESIGN_SETTINGS& bds = board->GetDesignSettings();
        bds.m_DRCEngine = std::make_shared<DRC_ENGINE>( board.get(), &bds );

        try
        {
            wxFileName rules( boardPath );
            rules.SetExt( FILEEXT::DesignRulesFileExtension );
            bds.m_DRCEngine->InitEngine( rules );
        }
        catch( const std::exception& exc )
        {
            printf( "warning: design rules failed to parse (%s); "
                    "routing with defaults\n", exc.what() );
        }
    }

    const int before = unroutedCount( board.get() );
    printf( "board            : %s\n", (const char*) boardPath.utf8_str() );
    printf( "nets             : %d\n", (int) board->GetNetInfo().GetNetCount() );
    printf( "unrouted (before): %d\n", before );

    HEADLESS_PNS_IFACE iface;
    PNS::ROUTER        router;

    iface.SetBoard( board.get() );
    router.SetInterface( &iface );
    router.ClearWorld();
    router.SyncWorld();

    PNS::ROUTING_SETTINGS settings( nullptr, "" );
    router.LoadSettings( &settings );
    router.Settings().SetMode( PNS::RM_Walkaround );

    printf( "world items      : %d joints\n", router.GetWorld()->JointCount() );

    int routed = 0;
    int refused = 0;
    int failed = 0;

    // Walkaround first (keeps existing copper untouched), then shove (allowed to
    // move it). Order matters: shove succeeds more often but disturbs work already
    // committed, so it is the fallback rather than the default.
    const std::vector<PNS::PNS_MODE> modes = { PNS::RM_Walkaround, PNS::RM_Shove };

    // Candidate layers: the pad's own layer first, then the other copper layers.
    // Single-layer routing is why the large boards closed nothing -- a blocked
    // direct path has no alternative without a layer change.
    auto layersFor =
            []( BOARD* aBoard, const TARGET& aTarget ) -> std::vector<PCB_LAYER_ID>
            {
                std::vector<PCB_LAYER_ID> out{ aTarget.layer };

                for( PCB_LAYER_ID layer : aBoard->GetEnabledLayers().CuStack() )
                {
                    if( layer != aTarget.layer )
                        out.push_back( layer );
                }

                return out;
            };

    std::vector<TARGET> ripupCandidates;
    int attempts = 0;

    std::vector<TARGET> targets = collectTargets( board.get() );
    printf( "candidates       : %d (shortest first)\n", (int) targets.size() );
    printf( "force-finish     : %s\n", forceFinish ? "yes" : "no" );

    for( const TARGET& target : targets )
    {
        if( routed >= wanted )
            break;

        int width = board->GetDesignSettings().GetCurrentTrackWidth();

        if( NETINFO_ITEM* ni = board->FindNet( target.netCode ) )
        {
            if( const NETCLASS* nc = ni->GetNetClass() )
                width = nc->GetTrackWidth();
        }

        // Populate sizes the way ROUTER_TOOL does rather than setting fields by
        // hand. ImportSizes() resolves track width, via size and diff-pair
        // width/gap from the netclass and design settings together, including
        // the "use netclass values" indirection; setting three fields manually
        // gets a self-consistent-looking SIZES_SETTINGS that is missing
        // everything else the placer reads.
        PNS::SIZES_SETTINGS sizes( router.Sizes() );

        iface.SetStartLayerFromPCBNew( target.layer );

        NETINFO_ITEM* startNet = board->FindNet( target.netCode );
        iface.ImportSizes( sizes, nullptr, startNet, VECTOR2D( target.a ) );

        // Vias need a layer pair to travel between.
        sizes.AddLayerPair( iface.GetPNSLayerFromBoardLayer( F_Cu ),
                            iface.GetPNSLayerFromBoardLayer( B_Cu ) );

        router.UpdateSizes( sizes );

        // Retry across strategies. A single attempt on one layer in one mode is
        // why the large boards closed nothing: the first path PNS tries is often
        // blocked, and without alternatives the connection is simply abandoned.
        bool ok = false;
        wxString how;

        // Differential pairs must be placed as a pair. Routed as two independent
        // single-ended tracks they end up adjacent but uncoupled, which DRC
        // reports as diff_pair_gap_out_of_range. DIFF_PAIR_PLACER finds the
        // partner itself from a start point near both pads.
        NETINFO_ITEM* thisNet = board->FindNet( target.netCode );
        NETINFO_ITEM* coupled = thisNet ? board->DpCoupledNet( thisNet ) : nullptr;

        std::vector<PNS::ROUTER_MODE> routerModes;

        if( coupled )
            routerModes.push_back( PNS::PNS_MODE_ROUTE_DIFF_PAIR );

        routerModes.push_back( PNS::PNS_MODE_ROUTE_SINGLE );

        for( PNS::ROUTER_MODE rmode : routerModes )
        {
        for( PNS::PNS_MODE mode : modes )
        {
            for( PCB_LAYER_ID layer : layersFor( board.get(), target ) )
            {
                attempts++;

                const int mark = iface.AddedCount();
                const int unroutedBefore = unroutedCount( board.get() );

                wxString why;

                if( !tryRoute( router, iface, target, mode, layer, forceFinish, rmode, &why,
                               board.get() ) )
                {
                    if( rmode == PNS::PNS_MODE_ROUTE_DIFF_PAIR )
                    {
                        printf( "  net %-4d : diffpair %s on %s: %s\n", target.netCode,
                                mode == PNS::RM_Walkaround ? "walkaround" : "shove",
                                (const char*) LSET::Name( layer ).utf8_str(),
                                (const char*) why.utf8_str() );
                    }

                    continue;
                }

                // PNS reporting success is not the same as the connection being
                // made. Accept only a route that actually reduced the unrouted
                // count; otherwise undo it and try the next strategy. Rolling
                // back also re-syncs the world so the next attempt sees a clean
                // board.
                if( unroutedCount( board.get() ) < unroutedBefore )
                {
                    ok = true;
                    how = wxString::Format(
                            wxT( "%s %s/%s" ),
                            rmode == PNS::PNS_MODE_ROUTE_DIFF_PAIR ? wxT( "diffpair" )
                                                                   : wxT( "single" ),
                            mode == PNS::RM_Walkaround ? wxT( "walkaround" ) : wxT( "shove" ),
                            LSET::Name( layer ) );
                    break;
                }

                if( rmode == PNS::PNS_MODE_ROUTE_DIFF_PAIR )
                {
                    printf( "  net %-4d : diffpair routed but closed nothing; rolled back\n",
                            target.netCode );
                }

                iface.RollbackTo( mark );
                router.ClearWorld();
                router.SyncWorld();
            }

            if( ok )
                break;
        }

        if( ok )
            break;
        }

        if( !ok )
        {
            failed++;
            ripupCandidates.push_back( target );
            continue;
        }

        routed++;
        printf( "  net %-4d : routed via %s\n", target.netCode,
                (const char*) how.utf8_str() );
    }

    // Length tuning: bring every routed net up to the longest one, which is what
    // a zero-skew budget asks for. Only lengthening is possible -- meanders cannot
    // shorten a net -- so the longest net sets the target.
    if( tune && routed > 0 && !haveProject )
    {
        printf( "tuning           : skipped (no project, so no netclasses)\n" );
    }
    else if( tune && routed > 0 )
    {
        std::set<int> nets;

        for( PCB_TRACK* track : board->Tracks() )
        {
            if( track->Type() == PCB_TRACE_T && track->GetNetCode() > 0 )
                nets.insert( track->GetNetCode() );
        }

        long long int target = 0;

        for( int net : nets )
            target = std::max( target, netLength( board.get(), net ) );

        printf( "tuning           : %d net(s) to %.3f mm\n", (int) nets.size(),
                target / 1e6 );

        int tuned = 0;

        for( int net : nets )
        {
            const long long int before_len = netLength( board.get(), net );

            if( before_len >= target )
                continue;

            long long int result = 0;

            if( tuneNet( router, board.get(), net, target, &result ) )
            {
                tuned++;
                printf( "  net %-4d : %.3f -> %.3f mm\n", net, before_len / 1e6,
                        netLength( board.get(), net ) / 1e6 );
            }
            else
            {
                printf( "  net %-4d : tuning failed (%.3f mm)\n", net, before_len / 1e6 );
            }

            router.ClearWorld();
            router.SyncWorld();
        }

        printf( "tuned            : %d\n", tuned );
        board->BuildConnectivity();
    }

    const int after = unroutedCount( board.get() );

    printf( "routed           : %d\n", routed );
    printf( "failed           : %d (after %d attempt(s))\n", failed, attempts );
    printf( "items added      : %d\n", iface.AddedCount() );
    printf( "items removed    : %d\n", iface.RemovedCount() );
    printf( "unrouted (after) : %d\n", after );
    printf( "delta            : %d\n", before - after );

    if( !outPath.IsEmpty() )
    {
        try
        {
            PCB_IO_KICAD_SEXPR io;
            io.SaveBoard( outPath, board.get(), nullptr );
            printf( "wrote            : %s\n", (const char*) outPath.utf8_str() );
        }
        catch( const std::exception& exc )
        {
            printf( "failed to save: %s\n", exc.what() );
            return 3;
        }
    }

    fflush( stdout );

    // Pgm owns a wxSingleInstanceChecker. The global PGM_BASE is destroyed
    // during static destruction, which happens *after* wxInitializer has shut
    // wx down -- freeing the checker then segfaults inside wxMBConv. Destroy it
    // here while wx is still alive.
    Pgm().Destroy();

    // Non-zero when nothing was routed, so a caller can gate on progress.
    return routed > 0 ? 0 : 4;
}
