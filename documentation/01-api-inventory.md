# API inventory

> **Generated file — do not edit.**
> `python documentation/tools/gen_api_inventory.py`
> KiCad `10.99.0-unknown` @ `a7c0a3e6ce` · regenerated 2026-08-20

Two independent facts, cross-referenced: what the protobuf schema *declares*, and what the C++ actually *handles*. A message existing in the schema does **not** mean KiCad answers it.

## Summary

| | Count |
|---|---|
| `.proto` files | 16 |
| Messages declared | 355 |
| Messages with a handler | 102 |
| Handler surfaces | 6 |
| `kicad-cli` subcommands | 10 |

Most declared messages are *types* and *responses*, which never need a handler. Handler count is the honest measure of the callable surface.

## Handler surfaces

Which handler answers a request depends on the document it targets. `EDITOR (base)` is inherited by both board and schematic handlers, which is why generic item CRUD works on either.

### COMMON — `common/api/api_handler_common.cpp`

*Always available, no document required* · **16 commands**

| Request | Response |
|---|---|
| `CloseAllDocuments` | `Empty` |
| `CloseDocument` | `Empty` |
| `ExpandTextVariables` | `ExpandTextVariablesResponse` |
| `GetKiCadBinaryPath` | `PathResponse` |
| `GetNetClasses` | `NetClassesResponse` |
| `GetPluginSettingsPath` | `StringResponse` |
| `GetTextAsShapes` | `GetTextAsShapesResponse` |
| `GetTextExtents` | `Box2` |
| `GetTextVariables` | `TextVariables` |
| `GetVersion` | `GetVersionResponse` |
| `ListLibraries` | `ListLibrariesResponse` |
| `OpenDocument` | `OpenDocumentResponse` |
| `Ping` | `Empty` |
| `RunTransmissionLineCalculation` | `RunTransmissionLineCalculationResponse` |
| `SetNetClasses` | `Empty` |
| `SetTextVariables` | `Empty` |

### EDITOR (base) — `common/api/api_handler_editor.cpp`

*Inherited by BOTH board and schematic handlers* · **8 commands**

| Request | Response |
|---|---|
| `BeginCommit` | `BeginCommitResponse` |
| `CreateItems` | `CreateItemsResponse` |
| `DeleteItems` | `DeleteItemsResponse` |
| `EndCommit` | `EndCommitResponse` |
| `GetTitleBlockInfo` | `TitleBlockInfo` |
| `HitTest` | `HitTestResponse` |
| `SetTitleBlockInfo` | `Empty` |
| `UpdateItems` | `UpdateItemsResponse` |

### BOARD — `pcbnew/api/api_handler_board.cpp`

*An open .kicad_pcb* · **22 commands**

| Request | Response |
|---|---|
| `AddToSelection` | `SelectionResponse` |
| `CheckPadstackPresenceOnLayers` | `PadstackPresenceResponse` |
| `ClearSelection` | `Empty` |
| `ExpandTextVariables` | `ExpandTextVariablesResponse` |
| `FlipItems` | `FlipItemsResponse` |
| `GetActiveLayer` | `BoardLayerResponse` |
| `GetBoardEnabledLayers` | `BoardEnabledLayersResponse` |
| `GetBoardStackup` | `BoardStackupResponse` |
| `GetBoundingBox` | `GetBoundingBoxResponse` |
| `GetGraphicsDefaults` | `GraphicsDefaultsResponse` |
| `GetItemsById` | `GetItemsResponse` |
| `GetPadShapeAsPolygon` | `PadShapeAsPolygonResponse` |
| `GetSelection` | `SelectionResponse` |
| `GetVisibleLayers` | `BoardLayers` |
| `InteractiveMoveItems` | `Empty` |
| `ParseAndCreateItemsFromString` | `CreateItemsResponse` |
| `RemoveFromSelection` | `SelectionResponse` |
| `RunAction` | `RunActionResponse` |
| `SaveDocumentToString` | `SavedDocumentResponse` |
| `SaveSelectionToString` | `SavedSelectionResponse` |
| `SetActiveLayer` | `Empty` |
| `SetVisibleLayers` | `Empty` |

### PCB APP — `pcbnew/api/api_handler_pcb.cpp`

*The pcbnew application itself* · **45 commands**

| Request | Response |
|---|---|
| `AutoplaceFootprints` | `AutoplaceFootprintsResponse` |
| `GetBoardDesignRules` | `BoardDesignRulesResponse` |
| `GetBoardEditorAppearanceSettings` | `BoardEditorAppearanceSettings` |
| `GetBoardLayerByName` | `BoardLayerResponse` |
| `GetBoardLayerName` | `BoardLayerNameResponse` |
| `GetBoardOrigin` | `Vector2` |
| `GetBoardPlotSettings` | `BoardPlotSettingsResponse` |
| `GetConnectedItems` | `GetItemsResponse` |
| `GetCustomDesignRules` | `CustomRulesResponse` |
| `GetItems` | `GetItemsResponse` |
| `GetItemsByNet` | `GetItemsResponse` |
| `GetItemsByNetClass` | `GetItemsResponse` |
| `GetNetClassForNets` | `NetClassForNetsResponse` |
| `GetNets` | `NetsResponse` |
| `GetOpenDocuments` | `GetOpenDocumentsResponse` |
| `GetPageSettings` | `PageSettings` |
| `ImportNetlist` | `ImportNetlistResponse` |
| `InjectDrcError` | `InjectDrcErrorResponse` |
| `RefillZones` | `Empty` |
| `RevertDocument` | `Empty` |
| `RunBoardJobExport3D` | `RunJobResponse` |
| `RunBoardJobExportDrill` | `RunJobResponse` |
| `RunBoardJobExportDxf` | `RunJobResponse` |
| `RunBoardJobExportGencad` | `RunJobResponse` |
| `RunBoardJobExportGerbers` | `RunJobResponse` |
| `RunBoardJobExportIpc2581` | `RunJobResponse` |
| `RunBoardJobExportIpcD356` | `RunJobResponse` |
| `RunBoardJobExportODB` | `RunJobResponse` |
| `RunBoardJobExportPdf` | `RunJobResponse` |
| `RunBoardJobExportPosition` | `RunJobResponse` |
| `RunBoardJobExportPs` | `RunJobResponse` |
| `RunBoardJobExportRender` | `RunJobResponse` |
| `RunBoardJobExportStats` | `RunJobResponse` |
| `RunBoardJobExportSvg` | `RunJobResponse` |
| `RunDrc` | `RunDrcResponse` |
| `SaveCopyOfDocument` | `Empty` |
| `SaveDocument` | `Empty` |
| `SearchFootprints` | `SearchFootprintsResponse` |
| `SetBoardDesignRules` | `BoardDesignRulesResponse` |
| `SetBoardEditorAppearanceSettings` | `Empty` |
| `SetBoardEnabledLayers` | `BoardEnabledLayersResponse` |
| `SetBoardOrigin` | `Empty` |
| `SetBoardPlotSettings` | `Empty` |
| `SetCustomDesignRules` | `CustomRulesResponse` |
| `SetPageSettings` | `PageSettings` |

### FOOTPRINT — `pcbnew/api/api_handler_footprint.cpp`

*The footprint editor* · **6 commands**

| Request | Response |
|---|---|
| `GetItems` | `GetItemsResponse` |
| `GetOpenDocuments` | `GetOpenDocumentsResponse` |
| `OpenLibraryItem` | `Empty` |
| `RevertDocument` | `Empty` |
| `SaveCopyOfDocument` | `Empty` |
| `SaveDocument` | `Empty` |

### SCHEMATIC — `eeschema/api/api_handler_sch.cpp`

*An open .kicad_sch* · **22 commands**

| Request | Response |
|---|---|
| `AddToSelection` | `SelectionResponse` |
| `ClearSelection` | `Empty` |
| `GetItems` | `GetItemsResponse` |
| `GetItemsById` | `GetItemsResponse` |
| `GetOpenDocuments` | `GetOpenDocumentsResponse` |
| `GetPageSettings` | `PageSettings` |
| `GetSchematicHierarchy` | `SchematicHierarchyResponse` |
| `GetSchematicNetlist` | `SchematicNetlistResponse` |
| `GetSelection` | `SelectionResponse` |
| `PlaceSymbol` | `PlaceSymbolResponse` |
| `RemoveFromSelection` | `SelectionResponse` |
| `RunErc` | `RunErcResponse` |
| `RunSchematicJobExportBOM` | `RunJobResponse` |
| `RunSchematicJobExportDxf` | `RunJobResponse` |
| `RunSchematicJobExportNetlist` | `RunJobResponse` |
| `RunSchematicJobExportPdf` | `RunJobResponse` |
| `RunSchematicJobExportPs` | `RunJobResponse` |
| `RunSchematicJobExportSvg` | `RunJobResponse` |
| `SaveCopyOfDocument` | `Empty` |
| `SaveDocument` | `Empty` |
| `SearchSymbols` | `SearchSymbolsResponse` |
| `SetPageSettings` | `PageSettings` |

## Schema by file

`H` marks a message with a registered handler.

### `board/board.proto`

package `kiapi.board` · 30 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `BoardFinish`  | — |
| `BoardImpedanceControl`  | — |
| `BoardEdgeConnector`  | — |
| `Castellation`  | — |
| `EdgePlating`  | — |
| `BoardEdgeSettings`  | — |
| `BoardStackupCopperLayer`  | — |
| `BoardStackupDielectricProperties`  | — |
| `BoardStackupDielectricLayer`  | — |
| `BoardStackupLayer`  | — |
| `BoardStackup`  | — |
| `BoardLayerGraphicsDefaults`  | — |
| `GraphicsDefaults`  | — |
| `BoardSettings`  | — |
| `MinimumConstraints`  | — |
| `PresetTrackWidth`  | — |
| `PresetViaDimension`  | — |
| `PresetDiffPairDimension`  | — |
| `PredefinedSizes`  | — |
| `SolderMaskPasteDefaults`  | — |
| `TeardropTargetParams`  | — |
| `TeardropTargetEntry`  | — |
| `TeardropDefaults`  | — |
| `ViaProtectionDefaults`  | — |
| `DrcSeveritySetting`  | — |
| `DrcExclusion`  | — |
| `CustomRuleDisallowSettings`  | — |
| `CustomRuleConstraint`  | — |
| `CustomRule`  | — |
| `BoardDesignRules`  | — |

### `board/board_commands.proto`

package `kiapi.board.commands` · 62 message(s) · 35 handled

| Message | Handled by |
|---|---|
| `GetBoardStackup` **H** | BOARD |
| `BoardStackupResponse`  | — |
| `UpdateBoardStackup`  | — |
| `GetBoardEnabledLayers` **H** | BOARD |
| `BoardEnabledLayersResponse`  | — |
| `SetBoardEnabledLayers` **H** | PCB APP |
| `GetGraphicsDefaults` **H** | BOARD |
| `GraphicsDefaultsResponse`  | — |
| `GetBoardDesignRules` **H** | PCB APP |
| `SetBoardDesignRules` **H** | PCB APP |
| `BoardDesignRulesResponse`  | — |
| `GetCustomDesignRules` **H** | PCB APP |
| `SetCustomDesignRules` **H** | PCB APP |
| `CustomRulesResponse`  | — |
| `GetBoardOrigin` **H** | PCB APP |
| `SetBoardOrigin` **H** | PCB APP |
| `GetBoardLayerName` **H** | PCB APP |
| `BoardLayerNameResponse`  | — |
| `GetBoardLayerByName` **H** | PCB APP |
| `GetNets` **H** | PCB APP |
| `NetsResponse`  | — |
| `GetItemsByNet` **H** | PCB APP |
| `GetItemsByNetClass` **H** | PCB APP |
| `GetConnectedItems` **H** | PCB APP |
| `GetNetClassForNets` **H** | PCB APP |
| `NetClassForNetsResponse`  | — |
| `ImportNetlist` **H** | PCB APP |
| `ImportNetlistResponse`  | — |
| `RefillZones` **H** | PCB APP |
| `GetPadShapeAsPolygon` **H** | BOARD |
| `PadShapeAsPolygonResponse`  | — |
| `CheckPadstackPresenceOnLayers` **H** | BOARD |
| `PadstackPresenceEntry`  | — |
| `PadstackPresenceResponse`  | — |
| `InjectDrcError` **H** | PCB APP |
| `InjectDrcErrorResponse`  | — |
| `GetVisibleLayers` **H** | BOARD |
| `BoardLayerResponse`  | — |
| `BoardLayers`  | — |
| `SetVisibleLayers` **H** | BOARD |
| `GetActiveLayer` **H** | BOARD |
| `SetActiveLayer` **H** | BOARD |
| `BoardEditorAppearanceSettings`  | — |
| `GetBoardEditorAppearanceSettings` **H** | PCB APP |
| `SetBoardEditorAppearanceSettings` **H** | PCB APP |
| `GetBoardPlotSettings` **H** | PCB APP |
| `SetBoardPlotSettings` **H** | PCB APP |
| `BoardPlotSettingsResponse`  | — |
| `FlipItems` **H** | BOARD |
| `ItemFlipResult`  | — |
| `FlipItemsResponse`  | — |
| `InteractiveMoveItems` **H** | BOARD |
| `DrcAffectedItem`  | — |
| `DrcViolation`  | — |
| `DrcIgnoredCheck`  | — |
| `RunDrc` **H** | PCB APP |
| `RunDrcResponse`  | — |
| `AutoplaceFootprints` **H** | PCB APP |
| `AutoplaceFootprintsResponse`  | — |
| `SearchFootprints` **H** | PCB APP |
| `FootprintSearchResult`  | — |
| `SearchFootprintsResponse`  | — |

### `board/board_jobs.proto`

package `kiapi.board.jobs` · 16 message(s) · 14 handled

| Message | Handled by |
|---|---|
| `BoardPlotSettings`  | — |
| `RunBoardJobExport3D` **H** | PCB APP |
| `RunBoardJobExportRender` **H** | PCB APP |
| `RunBoardJobExportSvg` **H** | PCB APP |
| `RunBoardJobExportDxf` **H** | PCB APP |
| `RunBoardJobExportPdf` **H** | PCB APP |
| `RunBoardJobExportPs` **H** | PCB APP |
| `RunBoardJobExportGerbers` **H** | PCB APP |
| `ExcellonFormatOptions`  | — |
| `RunBoardJobExportDrill` **H** | PCB APP |
| `RunBoardJobExportPosition` **H** | PCB APP |
| `RunBoardJobExportGencad` **H** | PCB APP |
| `RunBoardJobExportIpc2581` **H** | PCB APP |
| `RunBoardJobExportIpcD356` **H** | PCB APP |
| `RunBoardJobExportODB` **H** | PCB APP |
| `RunBoardJobExportStats` **H** | PCB APP |

### `board/board_types.proto`

package `kiapi.board.types` · 54 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `NetCode`  | — |
| `Net`  | — |
| `Track`  | — |
| `Arc`  | — |
| `ChamferedRectCorners`  | — |
| `ZoneConnectionSettings`  | — |
| `SolderMaskOverrides`  | — |
| `SolderPasteOverrides`  | — |
| `PadStackLayer`  | — |
| `PadStackOuterLayer`  | — |
| `DrillProperties`  | — |
| `PostMachiningProperties`  | — |
| `PadStack`  | — |
| `Via`  | — |
| `BoardGraphicShape`  | — |
| `Barcode`  | — |
| `BoardText`  | — |
| `BoardTextBox`  | — |
| `ThermalSpokeSettings`  | — |
| `SymbolPinInfo`  | — |
| `Pad`  | — |
| `HatchFillSettings`  | — |
| `ThievingFillSettings`  | — |
| `TeardropSettings`  | — |
| `CopperZoneSettings`  | — |
| `RuleAreaSettings`  | — |
| `ZoneBorderSettings`  | — |
| `ZoneFilledPolygons`  | — |
| `ZoneLayerProperties`  | — |
| `Zone`  | — |
| `AlignedDimensionAttributes`  | — |
| `OrthogonalDimensionAttributes`  | — |
| `RadialDimensionAttributes`  | — |
| `LeaderDimensionAttributes`  | — |
| `CenterDimensionAttributes`  | — |
| `Dimension`  | — |
| `ReferenceImage`  | — |
| `GridItemAffects`  | — |
| `CartesianGridItemAttributes`  | — |
| `PolarGridItemAttributes`  | — |
| `GridItem`  | — |
| `Group`  | — |
| `ConstraintMember`  | — |
| `Constraint`  | — |
| `FieldId`  | — |
| `Field`  | — |
| `FootprintAttributes`  | — |
| `NetTieDefinition`  | — |
| `FootprintDesignRuleOverrides`  | — |
| `Footprint3DModel`  | — |
| `JumperGroup`  | — |
| `JumperSettings`  | — |
| `Footprint`  | — |
| `FootprintInstance`  | — |

### `common/commands/base_commands.proto`

package `kiapi.common.commands` · 18 message(s) · 8 handled

| Message | Handled by |
|---|---|
| `GetVersion` **H** | COMMON |
| `GetVersionResponse`  | — |
| `Ping` **H** | COMMON |
| `GetKiCadBinaryPath` **H** | COMMON |
| `PathResponse`  | — |
| `GetTextExtents` **H** | COMMON |
| `TextOrTextBox`  | — |
| `GetTextAsShapes` **H** | COMMON |
| `TextWithShapes`  | — |
| `GetTextAsShapesResponse`  | — |
| `GetPluginSettingsPath` **H** | COMMON |
| `StringResponse`  | — |
| `TransmissionLineValue`  | — |
| `RunTransmissionLineCalculation` **H** | COMMON |
| `RunTransmissionLineCalculationResponse`  | — |
| `ListLibraries` **H** | COMMON |
| `LibraryInfo`  | — |
| `ListLibrariesResponse`  | — |

### `common/commands/editor_commands.proto`

package `kiapi.common.commands` · 44 message(s) · 25 handled

| Message | Handled by |
|---|---|
| `RefreshEditor`  | — |
| `OpenLibraryItem` **H** | FOOTPRINT |
| `GetOpenDocuments` **H** | PCB APP, FOOTPRINT, SCHEMATIC |
| `GetOpenDocumentsResponse`  | — |
| `SaveOptions`  | — |
| `SaveCopyOfDocument` **H** | PCB APP, FOOTPRINT, SCHEMATIC |
| `RevertDocument` **H** | PCB APP, FOOTPRINT |
| `RunAction` **H** | BOARD |
| `RunActionResponse`  | — |
| `BeginCommit` **H** | EDITOR (base) |
| `BeginCommitResponse`  | — |
| `EndCommit` **H** | EDITOR (base) |
| `EndCommitResponse`  | — |
| `CreateItems` **H** | EDITOR (base) |
| `ItemStatus`  | — |
| `ItemCreationResult`  | — |
| `CreateItemsResponse`  | — |
| `GetItems` **H** | PCB APP, FOOTPRINT, SCHEMATIC |
| `GetItemsById` **H** | BOARD, SCHEMATIC |
| `GetItemsResponse`  | — |
| `UpdateItems` **H** | EDITOR (base) |
| `ItemUpdateResult`  | — |
| `UpdateItemsResponse`  | — |
| `DeleteItems` **H** | EDITOR (base) |
| `ItemDeletionResult`  | — |
| `DeleteItemsResponse`  | — |
| `GetBoundingBox` **H** | BOARD |
| `GetBoundingBoxResponse`  | — |
| `GetSelection` **H** | BOARD, SCHEMATIC |
| `SelectionResponse`  | — |
| `AddToSelection` **H** | BOARD, SCHEMATIC |
| `RemoveFromSelection` **H** | BOARD, SCHEMATIC |
| `ClearSelection` **H** | BOARD, SCHEMATIC |
| `HitTest` **H** | EDITOR (base) |
| `HitTestResponse`  | — |
| `GetTitleBlockInfo` **H** | EDITOR (base) |
| `SetTitleBlockInfo` **H** | EDITOR (base) |
| `GetPageSettings` **H** | PCB APP, SCHEMATIC |
| `SetPageSettings` **H** | PCB APP, SCHEMATIC |
| `SaveDocumentToString` **H** | BOARD |
| `SavedDocumentResponse`  | — |
| `SaveSelectionToString` **H** | BOARD |
| `SavedSelectionResponse`  | — |
| `ParseAndCreateItemsFromString` **H** | BOARD |

### `common/commands/project_commands.proto`

package `kiapi.common.commands` · 12 message(s) · 9 handled

| Message | Handled by |
|---|---|
| `GetNetClasses` **H** | COMMON |
| `NetClassesResponse`  | — |
| `SetNetClasses` **H** | COMMON |
| `ExpandTextVariables` **H** | COMMON, BOARD |
| `ExpandTextVariablesResponse`  | — |
| `GetTextVariables` **H** | COMMON |
| `SetTextVariables` **H** | COMMON |
| `OpenDocument` **H** | COMMON |
| `OpenDocumentResponse`  | — |
| `CloseDocument` **H** | COMMON |
| `CloseAllDocuments` **H** | COMMON |
| `SaveDocument` **H** | PCB APP, FOOTPRINT, SCHEMATIC |

### `common/envelope.proto`

package `kiapi.common` · 5 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `ApiRequestHeader`  | — |
| `ApiRequest`  | — |
| `ApiResponseHeader`  | — |
| `ApiResponse`  | — |
| `ApiResponseStatus`  | — |

### `common/types/base_types.proto`

package `kiapi.common.types` · 42 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `CommandStatusResponse`  | — |
| `KiCadVersion`  | — |
| `KIID`  | — |
| `LibraryIdentifier`  | — |
| `SheetPath`  | — |
| `ProjectSpecifier`  | — |
| `DocumentSpecifier`  | — |
| `ItemHeader`  | — |
| `Vector2`  | — |
| `Vector3`  | — |
| `Vector3D`  | — |
| `Box2`  | — |
| `Distance`  | — |
| `Angle`  | — |
| `Ratio`  | — |
| `Time`  | — |
| `Color`  | — |
| `ArcStartMidEnd`  | — |
| `PolyLineNode`  | — |
| `PolyLine`  | — |
| `PolygonWithHoles`  | — |
| `PolySet`  | — |
| `TextAttributes`  | — |
| `Text`  | — |
| `TextBox`  | — |
| `StrokeAttributes`  | — |
| `GraphicFillAttributes`  | — |
| `GraphicAttributes`  | — |
| `GraphicSegmentAttributes`  | — |
| `GraphicRectangleAttributes`  | — |
| `GraphicArcAttributes`  | — |
| `GraphicCircleAttributes`  | — |
| `GraphicBezierAttributes`  | — |
| `GraphicEllipseAttributes`  | — |
| `GraphicEllipseArcAttributes`  | — |
| `GraphicShape`  | — |
| `CompoundShape`  | — |
| `TitleBlockInfo`  | — |
| `RuleCheckerMarkerID`  | — |
| `RuleCheckerMarker`  | — |
| `MinOptMax`  | — |
| `PageSettings`  | — |

### `common/types/enums.proto`

package `kiapi.common.types` · 0 message(s) · 0 handled

### `common/types/jobs.proto`

package `kiapi.common.types` · 2 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `RunJobResponse`  | — |
| `RunJobSettings`  | — |

### `common/types/project_settings.proto`

package `kiapi.common.project` · 4 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `NetClassBoardSettings`  | — |
| `NetClassSchematicSettings`  | — |
| `NetClass`  | — |
| `TextVariables`  | — |

### `common/types/wizards.proto`

package `kiapi.common.types` · 9 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `WizardIntParameter`  | — |
| `WizardRealParameter`  | — |
| `WizardBoolParameter`  | — |
| `WizardStringParameter`  | — |
| `WizardParameterList`  | — |
| `WizardParameter`  | — |
| `WizardInfo`  | — |
| `WizardMetaInfo`  | — |
| `WizardGeneratedContent`  | — |

### `schematic/schematic_commands.proto`

package `kiapi.schematic.types` · 14 message(s) · 5 handled

| Message | Handled by |
|---|---|
| `GetSchematicHierarchy` **H** | SCHEMATIC |
| `SchematicHierarchyResponse`  | — |
| `GetSchematicNetlist` **H** | SCHEMATIC |
| `SchematicNetlistResponse`  | — |
| `PlaceSymbol` **H** | SCHEMATIC |
| `PlaceSymbolResponse`  | — |
| `SearchSymbols` **H** | SCHEMATIC |
| `SymbolSearchResult`  | — |
| `SearchSymbolsResponse`  | — |
| `ErcAffectedItem`  | — |
| `ErcViolation`  | — |
| `ErcIgnoredCheck`  | — |
| `RunErc` **H** | SCHEMATIC |
| `RunErcResponse`  | — |

### `schematic/schematic_jobs.proto`

package `kiapi.schematic.jobs` · 10 message(s) · 6 handled

| Message | Handled by |
|---|---|
| `SchematicPlotSettings`  | — |
| `RunSchematicJobExportSvg` **H** | SCHEMATIC |
| `RunSchematicJobExportDxf` **H** | SCHEMATIC |
| `RunSchematicJobExportPdf` **H** | SCHEMATIC |
| `RunSchematicJobExportPs` **H** | SCHEMATIC |
| `RunSchematicJobExportNetlist` **H** | SCHEMATIC |
| `BOMFormatSettings`  | — |
| `BOMField`  | — |
| `BOMFieldSettings`  | — |
| `RunSchematicJobExportBOM` **H** | SCHEMATIC |

### `schematic/schematic_types.proto`

package `kiapi.schematic.types` · 34 message(s) · 0 handled

| Message | Handled by |
|---|---|
| `SchematicField`  | — |
| `SchematicLine`  | — |
| `Junction`  | — |
| `NoConnectMarker`  | — |
| `BusEntry`  | — |
| `SchematicText`  | — |
| `SchematicTextBox`  | — |
| `SchematicGraphicShape`  | — |
| `SchematicImage`  | — |
| `LocalLabel`  | — |
| `GlobalLabel`  | — |
| `HierarchicalLabel`  | — |
| `DirectiveLabel`  | — |
| `Group`  | — |
| `SheetPin`  | — |
| `SheetSymbol`  | — |
| `SchematicPinAlternate`  | — |
| `SchematicPin`  | — |
| `SchematicSymbolUnit`  | — |
| `SchematicSymbolBodyStyle`  | — |
| `SchematicSymbolChild`  | — |
| `SchematicSymbolAttributes`  | — |
| `SchematicSymbolVariant`  | — |
| `PinMapEntry`  | — |
| `PinMap`  | — |
| `AssociatedFootprint`  | — |
| `LibSymbolPinMaps`  | — |
| `PinMapInstanceOverride`  | — |
| `SchematicSymbol`  | — |
| `SchematicSymbolTransform`  | — |
| `SchematicSymbolInstance`  | — |
| `SheetInstance`  | — |
| `SchematicNetSheetContents`  | — |
| `SchematicNet`  | — |

## `kicad-cli` subcommands

Independent of the IPC API: a separate process, reading files from disk. Useful when no editor is running.

| Subcommand | Description |
|---|---|
| `api-server` | Run the KiCad IPC API server in headless mode |
| `fp` | Footprint and Footprint Libraries |
| `gerber` | View and compare existing Gerber/Excellon files. To export Gerbers from a PCB, use 'pcb export gerbers' |
| `import` | Import non-KiCad board and/or schematic files into a new KiCad project |
| `jobset` | Jobset |
| `mergetool` | Three-way merge driver for `git mergetool`. Detects PCB vs schematic vs library item from the output extension, opens the resolution dialog in the GUI kicad binary for any unresolved conflicts where available, and writes the merged file. |
| `pcb` | PCB |
| `sch` | Schematics |
| `sym` | Symbol and Symbol Libraries |
| `version` | Reports the version info in various formats |

## Reading this

- A message with no handler is **not callable**, however complete its schema looks.
- Requests are dispatched by fully-qualified proto name via `Any::ParseAnyTypeUrl()` matched against `RequestType().GetTypeName()` (`common/api/api_handler.cpp`), so the message name in this table is exactly what goes on the wire.
- See `03-gaps.md` for what is missing and what it blocks.
