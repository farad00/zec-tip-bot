import { copyFileSync, constants } from 'node:fs';
try {
  copyFileSync('.env.example', '.env', constants.COPYFILE_EXCL);
  console.log('Created .env. Enter only X_API_KEY and X_API_SECRET in that file locally, then build and authorize @zectipbot.');
} catch (error) {
  if (error.code === 'EEXIST') console.log('.env already exists; preserved without reading or printing it.');
  else throw new Error('Could not create the local configuration file.');
}
