import {createHash} from 'node:crypto';
import {readFile,realpath} from 'node:fs/promises';
import {resolve,sep} from 'node:path';

// The frontend build owns pages/dist only. Module logos and other declared
// package assets must survive a rebuild with hashes verified against their bytes.
export async function mergeManifestResources(packageRoot,existing,generated){
 const validate=resources=>{
  if(!Array.isArray(resources))throw Error('Invalid manifest resources');
  const seen=new Set();
  for(const resource of resources){
   if(!resource||Object.keys(resource).sort().join(',')!=='path,sha256'||typeof resource.path!=='string'||!/^[-a-zA-Z0-9_./]+$/.test(resource.path)||resource.path.split('/').some(part=>!part||part==='.'||part==='..')||typeof resource.sha256!=='string'||!/^[a-f0-9]{64}$/.test(resource.sha256)||seen.has(resource.path.toLowerCase()))throw Error('Invalid manifest resource');
   seen.add(resource.path.toLowerCase());
  }
 };
 validate(existing);validate(generated);
 if(generated.some(resource=>!resource.path.startsWith('pages/dist/')))throw Error('Unexpected generated resource');
 const root=await realpath(packageRoot),retained=[];
 for(const resource of existing.filter(resource=>!resource.path.startsWith('pages/dist/'))){
  const path=await realpath(resolve(root,resource.path));
  if(!path.startsWith(root+sep))throw Error('Manifest resource escapes package');
  retained.push({path:resource.path,sha256:createHash('sha256').update(await readFile(path)).digest('hex')});
 }
 const merged=[...retained,...generated.map(resource=>({...resource}))].sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);
 validate(merged);return merged;
}
