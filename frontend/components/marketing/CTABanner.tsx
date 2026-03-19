import { motion } from 'framer-motion';
import Link from 'next/link';
import { useScrollAnimation } from '../hooks/useScrollAnimation';

export function CTABanner() {
  const [ref, isInView] = useScrollAnimation();

  return (
    <section className="max-w-7xl mx-auto px-6 py-32">
      <motion.div
        ref={ref}
        initial={{ opacity: 0, y: 20 }}
        animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 20 }}
        transition={{ duration: 0.6 }}
        className="bg-gradient-to-r from-[#E879F9]/10 to-[#38BDF8]/10 border border-[#1E1E2E] rounded-2xl p-12 md:p-16 text-center"
      >
        <h2 className="text-[#FAF5FF] text-4xl md:text-5xl lg:text-6xl mb-8">
          Ready to meet your support mate?
        </h2>
        <Link
          href="/signup"
          className="bg-[#E879F9] text-[#0A0A0F] px-12 py-4 rounded-lg text-lg hover:bg-[#f099fb] hover:scale-105 transition-all inline-block"
        >
          Try for free
        </Link>
      </motion.div>
    </section>
  );
}