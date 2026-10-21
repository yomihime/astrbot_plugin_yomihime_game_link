import {exact,fail,validateCatalog,validateMasterKeyStatus} from './management-contract.js';
const operations=new Set(['catalog','read','update','recover','master-key-status','master-key-retry','master-key-restore','master-key-reset','master-key-adopt-environment','master-key-migration-plan','legacy-config-plan','legacy-config-execute']);
export function validateLegacyPlan(value,policyId,execute=false){
  if(!exact(value,execute?['policy_id','status','target_revision','migration_pending','written']:['policy_id','status','source_revision','target_revision','migration_pending','written'])||value.policy_id!==policyId||!['migration_pending','already_configured','not_applicable','copied'].includes(value.status)||!Number.isSafeInteger(value.target_revision)||value.target_revision<1||!execute&&(!Number.isSafeInteger(value.source_revision)||value.source_revision<1)||typeof value.migration_pending!=='boolean'||typeof value.written!=='boolean'||!execute&&value.written||value.migration_pending!==(value.status==='migration_pending'))fail('operation_unavailable');
  return Object.freeze({...value});
}
export function validateMigrationPlan(value){
  if(!exact(value,['operation_id','source_identity','generation','revision','inventory_digest','snapshot_digest','files_digest','file_count','source_retained','requires_stopped_host'])||
    typeof value.operation_id!=='string'||!value.operation_id||value.operation_id.length>128||typeof value.source_identity!=='string'||!value.source_identity||value.source_identity.length>256||
    ![value.generation,value.revision,value.file_count].every(v=>Number.isSafeInteger(v)&&v>=0)||
    ![value.inventory_digest,value.snapshot_digest,value.files_digest].every(v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v))||value.source_retained!==true||value.requires_stopped_host!==true)fail('operation_unavailable');
  return Object.freeze({...value});
}
/** Internal closed request layer. Never handed to Module pages. */
export function createManagementRequests(bridge,current){return async function post(endpoint,body,attempt){
  if(!operations.has(endpoint))fail('operation_unavailable');
  if(!current(attempt))fail('stale_context');const response=await bridge.apiPost(`admin/${endpoint}`,body);if(!current(attempt))fail('stale_context');
  if(endpoint==='catalog')return validateCatalog(response);if(endpoint==='read')return response;
  if(endpoint.startsWith('legacy-config-'))return validateLegacyPlan(response,body.policy_id,endpoint==='legacy-config-execute');
  if(endpoint==='master-key-migration-plan')return validateMigrationPlan(response);
  if(endpoint.startsWith('master-key-'))return validateMasterKeyStatus(response);
  if(endpoint==='update'&&(!exact(response,['module_id','revision'])||response.module_id!==body.module_id||!Number.isSafeInteger(response.revision)||response.revision<=body.expected_revision))fail('operation_unavailable');
  if(endpoint==='recover'&&(!exact(response,['recovered'])||response.recovered!==true))fail('operation_unavailable');return response;
};}
