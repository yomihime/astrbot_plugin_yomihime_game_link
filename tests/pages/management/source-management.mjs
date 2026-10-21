import {managementModule} from '../shell/source-build.mjs';
const module=await managementModule();
export const {createManagementPage,validateCatalog,validateSnapshot,updateBody,validateCredentialCatalog,validateCredentialStatus,credentialUpdateBody,validateMasterKeyStatus}=module;
