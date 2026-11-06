import {stripTypeScriptTypes,createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname,extname} from 'node:path';
const cache = new Map();
export async function sourceModule(path) {
  const full=resolve(root,path); if(cache.has(full)) return import(cache.get(full));
  const source=await readFile(full,'utf8');
  let output=extname(full)==='.json'
    ? `export default ${JSON.stringify(JSON.parse(source))}`
    : stripTypeScriptTypes(source,{mode:'strip',sourceUrl:full});
  const matches=[...output.matchAll(/from\s+(['"])([^'"]+)\1/g)];
  for(const match of matches) {
    const name=match[2];
    let url;
    if(name==='@game-link/ui'){
      const {sourceRuntimeURL}=await import('./source-build.mjs');
      url=await sourceRuntimeURL();
    }else{
      const target=name.startsWith('.')?resolve(dirname(full),name):createRequire(full).resolve(name);
      url=extname(target)==='.js'?pathToFileURL(target).href:await sourceURL(extname(target)?target:target+'.ts');
    }
    output=output.replace(match[0],`from ${JSON.stringify(url)}`);
  }
  const url='data:text/javascript;base64,'+Buffer.from(output).toString('base64'); cache.set(full,url); return import(url);
}
async function sourceURL(path) {await sourceModule(path);return cache.get(resolve(root,path));}
export const root=fileURLToPath(new URL('../../../',import.meta.url));
