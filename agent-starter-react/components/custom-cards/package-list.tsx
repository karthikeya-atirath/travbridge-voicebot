/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import { motion } from 'motion/react';
import { PackageCard, PackageData } from './package-card';

interface PackageListProps {
  data: any;
}

export function PackageList({ data }: PackageListProps) {
  // Parse the generic API response or the specific string format from the backend.
  let packages: PackageData[] = [];

  let sourceArray: any[] = [];
  if (Array.isArray(data)) {
    sourceArray = data;
  } else if (data && typeof data === 'object') {
    if (Array.isArray(data.body)) sourceArray = data.body;
    else if (Array.isArray(data.packages)) sourceArray = data.packages;
    else if (Array.isArray(data.data)) sourceArray = data.data;
    else if (data.itinerary_data)
      sourceArray = [data]; // single item
    else sourceArray = [data];
  }

  if (sourceArray.length > 0) {
    packages = sourceArray.map((item: any, idx: number) => {
      if (typeof item === 'string') {
        const nameMatch = item.match(/Package: (.*?)\./);
        const priceMatch = item.match(/Price: (\d+) rupees/);
        const durationMatch = item.match(/Duration: (\d+ days)/);
        const idMatch = item.match(/packageId: (PKG\d+)/);

        return {
          packageId: idMatch ? idMatch[1] : `PKG_${idx}`,
          packageName: nameMatch ? nameMatch[1] : 'Unknown Package',
          price: priceMatch ? Number(priceMatch[1]) : 0,
          duration: durationMatch ? durationMatch[1] : 'N/A',
        };
      }

      // Expected SOTC API format where data is inside itinerary_data
      if (item && item.itinerary_data) {
        const idata = item.itinerary_data;
        let firstImg = '';
        if (idata.constructed_images?.length) {
          firstImg = idata.constructed_images[0];
        } else if (idata.images?.length) {
          firstImg = idata.images[0];
        }
        return {
          packageId: idata.packageId || `PKG_${idx}`,
          packageName: idata.packageName || 'Unknown Package',
          price: idata.minimumPrice || idata.price || item.score || 0,
          duration: idata.days ? `${idata.days} days` : 'N/A',
          imageUrl: firstImg,
          itinerary: idata.packageItinerary?.itinerary || [],
          inclusions: idata.inclusions || '',
          exclusions: idata.exclusions || '',
          images: idata.constructed_images || idata.images || [],
        };
      }

      // Fallback for flat structure
      let flatImg = '';
      if (item?.constructed_images?.length) {
        flatImg = item.constructed_images[0];
      } else if (item?.images?.length) {
        flatImg = item.images[0];
      }
      return {
        packageId: item?.packageId || item?.id || `PKG_${idx}`,
        packageName: item?.packageName || item?.name || 'Unknown Package',
        price: item?.price || item?.startingPrice || item?.minimumPrice || 0,
        duration: item?.duration || item?.days ? `${item?.days} days` : 'N/A',
        imageUrl: flatImg,
        itinerary: item?.packageItinerary?.itinerary || [],
        inclusions: item?.inclusions || '',
        exclusions: item?.exclusions || '',
        images: item?.constructed_images || item?.images || [],
        ...item,
      };
    });
  }

  // Apply the slice cap after parsing all packages
  const finalPackages = packages.slice(0, 5);

  if (!finalPackages.length) {
    return (
      <div className="rounded-xl border border-neutral-200 bg-white/50 p-4 text-sm text-neutral-500 italic backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-900/50">
        No packages found for this request.
      </div>
    );
  }

  return (
    <div className="hide-scrollbar my-4 flex w-full max-w-full snap-x snap-mandatory items-stretch gap-6 overflow-x-auto px-4 pb-8">
      {finalPackages.map((pkg: PackageData, i: number) => (
        <motion.div
          key={pkg.packageId || Math.random()}
          className="flex h-full w-[350px] min-w-[350px] max-w-[350px] shrink-0 snap-start"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.08, duration: 0.4 }}
        >
          <PackageCard pkg={pkg} />
        </motion.div>
      ))}
    </div>
  );
}
